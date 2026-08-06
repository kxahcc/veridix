from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from runners.web.zap_connector import ZapDockerConnector
from runners.web.zap_connector import ZapConnector
from runners.web.connector_tool import ConnectorToolRunner
from services.agent_runtime.kernel.contracts import ExecutionRequest


class ZapMockHandler(BaseHTTPRequestHandler):
    spider_status_calls = 0

    def do_GET(self) -> None:
        if self.path.startswith("/JSON/core/view/version/"):
            self._send({"version": "2.15.0"})
            return
        if self.path.startswith("/JSON/spider/action/scan/"):
            self._send({"scan": "1"})
            return
        if self.path.startswith("/JSON/spider/view/status/"):
            type(self).spider_status_calls += 1
            status = (
                "100"
                if type(self).spider_status_calls > 1
                else "50"
            )
            self._send({"status": status})
            return
        if self.path.startswith("/JSON/alert/view/alerts/"):
            self._send(
                {
                    "alerts": [
                        {
                            "id": 7,
                            "alert": "XSS",
                            "method": "GET",
                            "url": "http://target.test/?q=x",
                            "evidence": "script",
                            "risk": "High",
                        }
                    ]
                }
            )
            return
        self._send({"error": "not found"}, 404)

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


def _start_server() -> tuple[ThreadingHTTPServer, str]:
    ZapMockHandler.spider_status_calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), ZapMockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(
                ("127.0.0.1", server.server_port),
                timeout=1.0,
            ):
                break
        except OSError:
            time.sleep(0.05)
    return server, f"http://127.0.0.1:{server.server_port}"


def test_zap_connector_health_spider_and_alerts() -> None:
    server, base_url = _start_server()
    try:
        connector = ZapConnector(base_url=base_url)

        assert connector.health()["version"] == "2.15.0"
        spider = connector.spider("http://target.test/")
        assert spider["status"] == "100"
        observations = connector.export_observations()

        assert observations[0]["request_id"] == "zap:7"
        assert observations[0]["vuln_category"] == "XSS"
        assert observations[0]["risk"] == "High"
    finally:
        server.shutdown()
        server.server_close()


class _FakeZapConnector:
    def __init__(self) -> None:
        self.mode = ""

    def spider(self, target: str) -> dict:
        self.mode = f"spider:{target}"
        return {"status": "100"}

    def active_scan(self, target: str) -> dict:
        self.mode = f"active:{target}"
        return {"status": "100"}

    def export_observations(self) -> list[dict]:
        return [
            {
                "request_id": "zap:7",
                "web_session_id": "zap-connector",
                "proxy_session_id": "zap-connector",
                "method": "GET",
                "url": "http://target.test/?q=x",
                "endpoint": "GET http://target.test/?q=x",
                "status_code": 0,
                "request_headers": {},
                "response_headers": {},
                "request_body": "",
                "response_body": "script",
                "content_type": "application/json",
                "request_size": 0,
                "response_size": 0,
                "artifact_ref": "artifact://zap/7",
                "redacted": False,
                "truncated": False,
                "vuln_category": "XSS",
                "risk": "High",
            }
        ]


def test_zap_connector_tool_runner_supports_spider_mode() -> None:
    connector = _FakeZapConnector()
    runner = ConnectorToolRunner(connector, "zap")

    result = runner.execute(
        ExecutionRequest(
            action_id="action_zap",
            run_id="run_zap",
            tool_ref="zap.scan",
            input={
                "target": "http://target.test/",
                "mode": "spider",
            },
            idempotency_key="run_zap:zap.scan:1",
        )
    )

    assert connector.mode == "spider:http://target.test/"
    assert result.status == "completed"
    assert result.observations[0]["vuln_category"] == "XSS"


def test_zap_connector_tool_runner_defaults_to_active() -> None:
    connector = _FakeZapConnector()
    runner = ConnectorToolRunner(connector, "zap")

    result = runner.execute(
        ExecutionRequest(
            action_id="action_zap",
            run_id="run_zap",
            tool_ref="zap.scan",
            input={"target": "http://target.test/"},
            idempotency_key="run_zap:zap.scan:1",
        )
    )

    assert connector.mode == "active:http://target.test/"
    assert result.status == "completed"


@pytest.mark.integration
def test_zap_docker_connector_starts_when_enabled() -> None:
    if os.environ.get("VERIDIX_ZAP_DOCKER") != "1":
        pytest.skip("set VERIDIX_ZAP_DOCKER=1 to run the real ZAP container")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    connector = ZapDockerConnector(port=port)
    try:
        connector.start()
        assert connector.health().get("version")
    finally:
        connector.stop()
