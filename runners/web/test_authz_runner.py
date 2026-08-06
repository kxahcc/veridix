from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.agent_runtime.kernel.contracts import ExecutionRequest
from runners.web.authz_runner import AuthzMatrixRunner


class AuthzHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        token = str(self.headers.get("Authorization", "")).removeprefix(
            "Bearer "
        )
        if token == "admin-token":
            body = {"user": {"id": "user_2", "role": "admin"}}
            status = 200
        elif token == "user-token":
            body = {"user": {"id": "user_1", "role": "user"}}
            status = 200
        else:
            body = {"error": "forbidden"}
            status = 403
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # pragma: no cover
        return


def _request(endpoint: str, low_token: str) -> ExecutionRequest:
    return ExecutionRequest(
        action_id="action_authz",
        run_id="run_authz",
        tool_ref="web.authz.test",
        input={
            "endpoint": endpoint,
            "method": "GET",
            "low_privilege_token": low_token,
            "high_privilege_token": "admin-token",
            "object_id": "user_2",
        },
        idempotency_key="run_authz:1",
    )


def test_authz_runner_detects_idor_candidate() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), AuthzHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/api/users/user_2"
        runner = AuthzMatrixRunner(timeout=5)

        result = runner.execute(_request(endpoint, "user-token"))

        assert result.status == "completed"
        assert result.observations[0]["vuln_category"] == "IDOR"
        assert result.observations[0]["authz_status"] == "allowed"
        assert result.artifact_refs
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_authz_runner_negative_when_low_token_denied() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), AuthzHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/api/users/user_2"
        runner = AuthzMatrixRunner(timeout=5)

        result = runner.execute(_request(endpoint, "no-token"))

        assert result.status == "completed"
        assert result.observations[0].get("vuln_category") is None
        assert result.observations[0]["authz_status"] == "denied"
    finally:
        server.shutdown()
        thread.join(timeout=2)
