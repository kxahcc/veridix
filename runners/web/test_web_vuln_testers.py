from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runners.web.web_vuln_testers import FileUploadTesterRunner, LFITesterRunner
from services.agent_runtime.kernel.contracts import ExecutionRequest


UPLOADED: dict[str, str] = {}


class VeridixTestServer(ThreadingHTTPServer):
    def shutdown(self) -> None:
        super().shutdown()
        self.server_close()


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/login.php":
            self._send(
                "<form><input name='user_token' value='abcd1234'></form>"
            )
        elif self.path == "/security.php":
            self._send(
                "<form><input name='user_token' value='ef567890'></form>"
            )
        elif self.path == "/vulnerabilities/upload/":
            self._send(
                "<form><input name='user_token' value='1234abcd'></form>"
            )
        elif self.path.startswith("/hackable/uploads/pwn_"):
            marker = self.path.rsplit("_", 1)[-1].removesuffix(".php")
            self._send(f"<?php echo '{marker}'; ?>")
        elif self.path.startswith("/vulnerabilities/fi/"):
            self._send("root:x:0:0:root:/root:/bin/bash")
        else:
            self._send("not found", 404)

    def do_POST(self) -> None:
        if self.path == "/login.php":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", "replace")
            if "password=password" in body or "pwd=password" in body:
                self._send("Welcome")
            else:
                self._send("Login failed")
        elif self.path == "/security.php":
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self._send("Security level set")
        elif self.path == "/vulnerabilities/upload/":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            if "pwn_" in raw:
                UPLOADED["shell"] = raw
                self._send("succesfully uploaded")
            else:
                self._send("upload failed")
        else:
            self._send("not found", 404)

    def log_message(self, *args) -> None:  # pragma: no cover
        return


def _server() -> tuple[ThreadingHTTPServer, str]:
    server = VeridixTestServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_file_upload_tester_detects_rce() -> None:
    server, base = _server()
    try:
        result = FileUploadTesterRunner().execute(
            ExecutionRequest(
                action_id="upload_1",
                run_id="run_upload_1",
                idempotency_key="upload_1",
                tool_ref="web.file-upload.test",
                input={"target": base},
            )
        )
    finally:
        server.shutdown()

    assert result.status == "completed"
    assert result.observations[0]["vuln_category"] == "RCE"
    assert result.observations[0]["marker_present"] is True
    assert result.observations[0]["replay_proof"]["shell_url"].startswith(base)


def test_lfi_tester_detects_file_read() -> None:
    server, base = _server()
    try:
        result = LFITesterRunner().execute(
            ExecutionRequest(
                action_id="lfi_1",
                run_id="run_lfi_1",
                idempotency_key="lfi_1",
                tool_ref="web.lfi.test",
                input={"target": base},
            )
        )
    finally:
        server.shutdown()

    assert result.status == "completed"
    assert result.observations[0]["vuln_category"] == "LFI"
    assert result.observations[0]["has_file_content"] is True
    assert result.observations[0]["replay_proof"]["file"] == "/etc/passwd"


def test_tester_results_serialize() -> None:
    server, base = _server()
    try:
        result = FileUploadTesterRunner().execute(
            ExecutionRequest(
                action_id="upload_2",
                run_id="run_upload_2",
                idempotency_key="upload_2",
                tool_ref="web.file-upload.test",
                input={"target": base},
            )
        )
        json.dumps(
            {
                "status": result.status,
                "observation": result.observations[0],
            }
        )
    finally:
        server.shutdown()


def test_generic_login_fields_success() -> None:
    server, base = _server()
    try:
        from runners.web.web_vuln_testers import _login
        import httpx

        with httpx.Client(
            base_url=base,
            timeout=5,
            verify=False,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            ok = _login(
                client,
                username="admin",
                password="password",
                login_path="/login.php",
                login_fields={
                    "log": "{username}",
                    "pwd": "{password}",
                    "wp-submit": "Log In",
                    "testcookie": "1",
                },
            )
            assert ok is True
    finally:
        server.shutdown()


def test_generic_login_fields_wrong_password_fails() -> None:
    server, base = _server()
    try:
        from runners.web.web_vuln_testers import _login
        import httpx

        with httpx.Client(
            base_url=base,
            timeout=5,
            verify=False,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            ok = _login(
                client,
                username="admin",
                password="wrong",
                login_path="/login.php",
                login_fields={
                    "log": "{username}",
                    "pwd": "{password}",
                    "wp-submit": "Log In",
                    "testcookie": "1",
                },
            )
            assert ok is False
    finally:
        server.shutdown()
