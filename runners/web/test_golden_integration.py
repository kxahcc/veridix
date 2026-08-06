from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from runners.web.browser_session import BrowserSessionManager
from runners.web.golden import GoldenWebSlice
from runners.web.proxy_gateway import ProxyGateway, RequestStore

ROOT = Path(__file__).resolve().parents[2]


class GoldenHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, "application/json", json.dumps({"status": "ok"}))
        elif self.path == "/login":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "session=secret-abc; Path=/")
            body = "<html>token=secret-value</html>"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
        elif self.path.startswith("/profile"):
            role = "admin" if "role=admin" in self.path else "user"
            self._send(
                200,
                "application/json",
                json.dumps({"user": role, "token": "secret-value"}),
            )
        else:
            self._send(404, "application/json", json.dumps({"error": "not_found"}))

    def _send(self, status: int, content_type: str, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _chromium_executable() -> str | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    candidates = sorted(
        base.glob("chromium-*/chrome-win/chrome.exe"),
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


@pytest.mark.integration
def test_golden_slice_browser_proxy_capture_and_diff() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), GoldenHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    proxy_port = _free_port()
    gateway: ProxyGateway | None = None
    sessions = BrowserSessionManager()
    tmpdir: str | None = None
    try:
        (Path(ROOT) / ".tmp").mkdir(exist_ok=True)
        tmpdir = tempfile.mkdtemp(prefix="golden_", dir=Path(ROOT) / ".tmp")
        out_path = Path(tmpdir) / "capture.jsonl"
        gateway = ProxyGateway(str(ROOT))
        gateway.start(
            listen_port=proxy_port,
            out_path=out_path,
            confdir=Path(tmpdir),
        )
        gateway.wait_ready(base_url)

        handle = sessions.open(
            session_id="browser_golden",
            proxy_url=f"http://127.0.0.1:{proxy_port}",
            executable_path=_chromium_executable(),
        )
        sessions.navigate(handle, f"{base_url}/login")
        sessions.navigate(handle, f"{base_url}/profile?role=user")
        sessions.close(handle)
        gateway.stop()
        gateway = None

        store = RequestStore.load(str(out_path))
        observations = store.records()
        assert any("/login" in record.url for record in observations)
        profile = next(record for record in observations if "/profile" in record.url)
        login = next(record for record in observations if "/login" in record.url)
        assert "secret-value" not in profile.response_body
        cookie = next(
            (
                value
                for key, value in profile.request_headers.items()
                if key.lower() == "cookie"
            ),
            None,
        )
        assert cookie == "[REDACTED:cookie]"
        assert "[REDACTED:set-cookie]" in login.response_headers.values()

        result = GoldenWebSlice(
            candidate_path="/profile",
            mutation_param="role",
            mutation_value="admin",
        ).run(observations, base_url=base_url)

        assert result.candidate.diff is not None
        assert result.candidate.diff.changed is True
        assert result.candidate.replay_proof is not None
        assert any(
            "/profile" in endpoint for endpoint in result.endpoint_model.endpoints
        )
    finally:
        if gateway is not None:
            gateway.stop()
        server.shutdown()
        thread.join(timeout=5)
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
