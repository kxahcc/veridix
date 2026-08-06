from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from runners.container.resource_handle import ResourceManager, ResourceStatus
from runners.web.browser_session import BrowserSessionManager
from runners.web.proxy_gateway import ProxyGateway
from runners.web.recovery import RecoveryLog, RecoveryRecord, decide_recovery

ROOT = Path(__file__).resolve().parents[2]


def test_recovery_decision_for_each_state() -> None:
    manager = ResourceManager()
    handle = manager.create("sbx", "browser")
    assert decide_recovery(handle, reconnect_capability=True).action == "unavailable"

    manager.mark_ready("sbx")
    assert decide_recovery(handle, reconnect_capability=True).action == "reuse"

    manager.attach("sbx")
    manager.detach("sbx")
    assert decide_recovery(handle, reconnect_capability=True).action == "reconnect"
    assert decide_recovery(handle, reconnect_capability=False).action == "unavailable"

    manager.attach("sbx")
    manager._transition(handle, ResourceStatus.LOST, "lost")
    assert decide_recovery(handle, reconnect_capability=True).action == "rebuild"


def test_recovery_log_records_and_emits_run_events(tmp_path) -> None:
    log_path = tmp_path / "recovery.jsonl"
    log = RecoveryLog(log_path)
    log.append(
        RecoveryRecord(
            resource_id="browser_1",
            resource_type="browser",
            action="rebuild",
            reason="resource_lost",
            from_status="lost",
            new_resource_id="browser_1_recovered",
            reobserve_required=True,
            run_id="run_1",
        )
    )

    assert len(log.records()) == 1
    events = log.to_run_events("run_1")
    assert events[0].event_type == "resource.recovered"
    assert events[0].payload["reobserve_required"] is True
    assert events[0].payload["new_resource_id"] == "browser_1_recovered"

    loaded = RecoveryLog.load(log_path)
    assert loaded.records()[0].resource_id == "browser_1"
    assert loaded.records()[0].run_id == "run_1"


def _chromium_executable() -> str | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    candidates = sorted(
        base.glob("chromium-*/chrome-win/chrome.exe"),
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


@pytest.mark.integration
def test_browser_session_rebuilds_after_loss() -> None:
    sessions = BrowserSessionManager()
    executable = _chromium_executable()
    proxy = "http://127.0.0.1:1"
    log = RecoveryLog()
    try:
        handle = sessions.open(
            session_id="browser_1",
            proxy_url=proxy,
            executable_path=executable,
        )
        sessions.navigate(handle, "data:text/html,<html>first</html>")
        sessions.close(handle)

        rebuilt = sessions.recover(
            handle,
            proxy_url=proxy,
            executable_path=executable,
            log=log,
            run_id="run_recovery_browser",
        )
        assert rebuilt.resource_id == "browser_1_recovered"
        assert rebuilt.metadata.get("reobserve_required") is True
        assert log.records()[0].action == "rebuild"
        assert log.records()[0].reobserve_required is True
        assert log.records()[0].run_id == "run_recovery_browser"
        sessions.navigate(rebuilt, "data:text/html,<html>rebuilt</html>")
        sessions.close(rebuilt)
    finally:
        # If a rebuild leaked, close the manager's remaining browser.
        try:
            sessions.close(sessions._resources.get("browser_1"))
        except Exception:
            pass


@pytest.mark.integration
def test_proxy_gateway_restart_recovers() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps({"status": "ok"})
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:  # pragma: no cover - test noise
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    tmpdir = Path(tempfile.mkdtemp(prefix="proxy_recovery_", dir=ROOT / ".tmp"))
    gateway = ProxyGateway(str(ROOT))
    log = RecoveryLog()
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            proxy_port = sock.getsockname()[1]
        out_path = tmpdir / "capture.jsonl"
        base = f"http://127.0.0.1:{server.server_port}"

        gateway.start(listen_port=proxy_port, out_path=out_path, confdir=tmpdir)
        gateway.wait_ready(base)
        gateway.restart(
            listen_port=proxy_port,
            out_path=out_path,
            confdir=tmpdir,
            log=log,
            run_id="run_recovery_proxy",
        )
        gateway.wait_ready(base)
        gateway.stop()
        assert log.records()[0].action == "restart"
        assert log.records()[0].reobserve_required is True
        assert log.records()[0].run_id == "run_recovery_proxy"
    finally:
        gateway.stop()
        server.shutdown()
        thread.join(timeout=5)
