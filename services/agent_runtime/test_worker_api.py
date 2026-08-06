from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from services.agent_runtime.app.main import app
from services.agent_runtime.app.runner_factory import (
    build_worker_runner_factory,
)
from services.agent_runtime.kernel.composite_tool_runner import (
    CompositeToolRunner,
)
from services.agent_runtime.kernel.fake_runner import FakeRunner


class MockControlHandler(BaseHTTPRequestHandler):
    captured: list[dict] = []

    def do_GET(self) -> None:
        if self.path == "/api/v1/runs":
            payload = json.dumps([]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).captured.append({"path": self.path, "body": body})
        payload = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


def _mock_control():
    MockControlHandler.captured = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockControlHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}", thread


def test_agent_worker_relays_event_to_control_plane(monkeypatch) -> None:
    server, control_url, thread = _mock_control()
    monkeypatch.setenv("VERIDIX_CONTROL_URL", control_url)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/runs/run_1/events",
                json={
                    "event_id": "harness.snapshot:run_1:abc",
                    "event_type": "harness.snapshot",
                    "payload": {"harness_digest": "h"},
                },
            )

        assert response.status_code == 200
        assert len(MockControlHandler.captured) == 1
        forwarded = MockControlHandler.captured[0]["body"]
        assert forwarded["actor"] == "agent-worker"
        assert forwarded["event_type"] == "harness.snapshot"
        assert forwarded["payload"]["harness_digest"] == "h"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_agent_worker_rejects_unlisted_event_type(monkeypatch) -> None:
    server, control_url, thread = _mock_control()
    monkeypatch.setenv("VERIDIX_CONTROL_URL", control_url)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/runs/run_1/events",
                json={
                    "event_id": "custom.event:run_1:abc",
                    "event_type": "custom.event",
                    "payload": {},
                },
            )
        assert response.status_code == 400
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_agent_worker_requires_control_url(monkeypatch) -> None:
    monkeypatch.delenv("VERIDIX_CONTROL_URL", raising=False)
    with TestClient(app) as client:
        response = client.post(
            "/runs/run_1/events",
            json={
                "event_id": "tool.failed:run_1:abc",
                "event_type": "tool.failed",
                "payload": {},
            },
        )
    assert response.status_code == 503


def test_worker_runner_factory_fake_mode() -> None:
    factory = build_worker_runner_factory(runner_kind="fake")

    runner = factory()

    from services.agent_runtime.kernel.composite_tool_runner import (
        CompositeToolRunner,
    )

    assert isinstance(runner, CompositeToolRunner)
    assert isinstance(runner._default, FakeRunner)
    assert {
        "web.graphql.test",
        "web.websocket.test",
        "web.authz.test",
        "web.ssrf.test",
        "oast.create",
    }.issubset(runner._runners)


def test_worker_runner_factory_docker_fails_closed() -> None:
    def broken_docker() -> object:
        raise RuntimeError("no docker daemon")

    with pytest.raises(RuntimeError, match="docker runner unavailable"):
        build_worker_runner_factory(
            runner_kind="docker",
            docker_backend_factory=broken_docker,
        )


def test_worker_runner_factory_docker_wires_browser_and_shell() -> None:
    class StubBackend:
        pass

    class StubWeb:
        pass

    factory = build_worker_runner_factory(
        runner_kind="docker",
        docker_backend_factory=lambda: StubBackend(),
        web_runner=StubWeb(),
    )

    runner = factory()

    assert isinstance(runner, CompositeToolRunner)
    assert {
        "shell.probe",
        "nmap.scan",
        "nuclei.scan",
        "metasploit.console",
        "fscan.scan",
        "masscan.scan",
        "web.sqlmap.scan",
        "code.sast.semgrep",
        "binary.strings",
        "zap.scan",
        "caido.scan",
        "burp.scan",
        "browser.open",
        "proxy.list",
        "web.replay",
    } <= set(runner._runners)


def test_worker_autopilot_starts_when_enabled(monkeypatch) -> None:
    server, control_url, thread = _mock_control()
    monkeypatch.setenv("VERIDIX_CONTROL_URL", control_url)
    monkeypatch.setenv("VERIDIX_WORKER_AUTOPILOT", "1")
    monkeypatch.setenv("VERIDIX_PROVIDER_ENDPOINT", "http://provider.test/v1")
    monkeypatch.setenv("VERIDIX_PROVIDER_MODEL", "mock")
    monkeypatch.setenv("VERIDIX_RUNNER", "fake")
    try:
        with TestClient(app) as client:
            assert client.get("/healthz").status_code == 200
            time.sleep(0.3)
    finally:
        server.shutdown()
        thread.join(timeout=2)
