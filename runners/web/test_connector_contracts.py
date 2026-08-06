from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runners.web.burp_connector import BurpConnector
from runners.web.caido_connector import CaidoConnector
from runners.web.connector_tool import (
    ConnectorToolRunner,
    UnavailableToolRunner,
)
from services.agent_runtime.kernel.contracts import ExecutionRequest


def _wait_ready(server: ThreadingHTTPServer, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(
                ("127.0.0.1", server.server_port),
                timeout=1.0,
            ):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("mock server did not become ready")


class CaidoMockHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/graphql":
            self._send({"errors": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        query = body.get("query", "")
        if "createScan" in query:
            self._send(
                {
                    "data": {
                        "createScan": {
                            "scan": {"id": "scan_1"}
                        }
                    }
                }
            )
            return
        if "ScanStatus" in query:
            self._send(
                {
                    "data": {
                        "scan": {
                            "id": "scan_1",
                            "status": "COMPLETED",
                        }
                    }
                }
            )
            return
        if "Issues" in query:
            self._send(
                {
                    "data": {
                        "issues": {
                            "nodes": [
                                {
                                    "id": 11,
                                    "title": "SQL Injection",
                                    "severity": "high",
                                    "path": "https://target.test/?id=1",
                                    "detail": "injection evidence",
                                }
                            ]
                        }
                    }
                }
            )
            return
        self._send({"data": {"__typename": "Query"}})

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


class BurpMockHandler(BaseHTTPRequestHandler):
    events: list[dict] = []

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send({"ok": True})
            return
        if self.path == "/issues":
            self._send(
                {
                    "issues": [
                        {
                            "id": 3,
                            "issue": "SQL Injection",
                            "severity": "high",
                            "method": "GET",
                            "url": "https://target.test/?id=1",
                            "detail": "burp evidence",
                        }
                    ]
                }
            )
            return
        self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/scans":
            self._send({"scan_id": "burp_scan_1"})
            return
        if self.path == "/events":
            type(self).events.append(body)
            self._send({"accepted": True})
            return
        self._send({"error": "not found"}, 404)

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


def test_caido_connector_health_scan_and_observations() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), CaidoMockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_ready(server)
    try:
        connector = CaidoConnector(
            base_url=f"http://127.0.0.1:{server.server_port}"
        )

        assert connector.health()["__typename"] == "Query"
        scan_id = connector.create_scan("https://target.test/")
        assert scan_id == "scan_1"
        assert connector.scan_status(scan_id)["status"] == "COMPLETED"
        observations = connector.export_observations()

        assert observations[0]["request_id"] == "caido:11"
        assert observations[0]["vuln_category"] == "SQL Injection"
        assert observations[0]["risk"] == "high"
    finally:
        server.shutdown()
        server.server_close()


def test_burp_connector_health_scan_observations_and_event_bridge() -> None:
    BurpMockHandler.events = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), BurpMockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_ready(server)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        connector = BurpConnector(
            base_url=base,
            api_token="token",
            event_bridge_url=f"{base}/events",
        )

        assert connector.health()["ok"] is True
        assert connector.start_scan("https://target.test/") == "burp_scan_1"
        observations = connector.export_observations()
        connector.send_event({"issue": "new"})

        assert observations[0]["request_id"] == "burp:3"
        assert observations[0]["vuln_category"] == "SQL Injection"
        assert BurpMockHandler.events == [{"issue": "new"}]
    finally:
        server.shutdown()
        server.server_close()


def test_connector_tool_runner_exports_normalized_observations() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), CaidoMockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_ready(server)
    try:
        runner = ConnectorToolRunner(
            CaidoConnector(
                base_url=f"http://127.0.0.1:{server.server_port}"
            ),
            "caido",
        )
        result = runner.execute(
            ExecutionRequest(
                action_id="action_1",
                run_id="run_1",
                tool_ref="caido.scan",
                input={"target": "https://target.test/"},
                idempotency_key="run_1:caido.scan:1",
            )
        )

        assert result.status == "completed"
        assert result.observations[0]["vuln_category"] == "SQL Injection"
    finally:
        server.shutdown()
        server.server_close()


def test_unavailable_connector_runner_fails_closed() -> None:
    result = UnavailableToolRunner("burp.scan").execute(
        ExecutionRequest(
            action_id="action_1",
            run_id="run_1",
            tool_ref="burp.scan",
            input={"target": "https://target.test/"},
            idempotency_key="run_1:burp.scan:1",
        )
    )

    assert result.status == "failed"
    assert "not configured" in result.stderr
