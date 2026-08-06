from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runners.web.owasp_tester import OwaspTesterRunner
from services.agent_runtime.kernel.contracts import ExecutionRequest


GUESTBOOK: list[bytes] = []


class VeridixTestServer(ThreadingHTTPServer):
    def shutdown(self) -> None:
        super().shutdown()
        self.server_close()


class Handler(BaseHTTPRequestHandler):
    def _send(
        self,
        body: str,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path in ("/login.php", "/wp-login.php"):
            self._send(
                "<form><input name='user_token' value='abcd1234'></form>"
            )
        elif self.path == "/security.php":
            self._send(
                "<form><input name='user_token' value='ef567890'></form>"
            )
        elif self.path == "/vulnerabilities/exec/":
            self._send(
                "<form><input name='user_token' value='1234abcd'></form>"
            )
        elif self.path == "/config/config.inc.php.bak":
            self._send("$DB_PASSWORD = 'secret';")
        elif self.path.startswith("/vulnerabilities/xss_r/"):
            if "N3TSEC" in self.path:
                self._send("hello N3TSEC<script>alert(1)</script>")
            else:
                self._send("hello")
        elif self.path.startswith("/vulnerabilities/xss_s/"):
            self._send(b"<br>".join(GUESTBOOK).decode("utf-8", "replace"))
        elif self.path.startswith("/vulnerabilities/csrf/"):
            if "Change=Change" in self.path:
                self._send("Password Changed")
            else:
                self._send("form")
        elif self.path.startswith("/custom-csrf/"):
            if "Change=Change" in self.path:
                self._send("Password Changed")
            else:
                self._send("form")
        elif self.path.startswith("/setup.php"):
            self._send("PHP version 7.0.30")
        elif self.path.startswith("/external/phpids"):
            self._send("phpids log 172.18.0.1")
        else:
            self._send("not found", 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", "replace")
        if self.path == "/login.php":
            if "password=password" in body:
                self._send("Welcome")
            else:
                self._send("Login failed")
        elif self.path == "/security.php":
            self._send("ok")
        elif self.path == "/vulnerabilities/exec/":
            if "N3TSEC" in body:
                marker = next(
                    (part for part in body.split("&") if "N3TSEC" in part),
                    "N3TSEC",
                )
                self._send(f"output {marker}")
            else:
                self._send("no marker")
        elif self.path == "/vulnerabilities/xss_s/":
            if "N3TSEC" in body:
                GUESTBOOK.append(b"N3TSEC<script>alert(2)</script>")
            self._send("saved")
        else:
            self._send("not found", 404)

    def log_message(self, *args) -> None:  # pragma: no cover
        return


def _server() -> tuple[ThreadingHTTPServer, str]:
    server = VeridixTestServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _run(base: str, check: str, extra: dict | None = None) -> object:
    return OwaspTesterRunner().execute(
        ExecutionRequest(
            action_id=f"owasp_{check}",
            run_id=f"run_{check}",
            idempotency_key=check,
            tool_ref="web.owasp.test",
            input={"target": base, "check": check, **(extra or {})},
        )
    )


def test_command_injection_check() -> None:
    server, base = _server()
    try:
        result = _run(base, "command_injection")
    finally:
        server.shutdown()
    assert result.observations[0]["vuln_category"] == "CommandInjection"


def test_backup_file_check() -> None:
    server, base = _server()
    try:
        result = _run(base, "backup_file")
    finally:
        server.shutdown()
    assert result.observations[0]["vuln_category"] == "InformationDisclosure"


def test_reflected_xss_check() -> None:
    server, base = _server()
    try:
        result = _run(base, "xss_reflected")
    finally:
        server.shutdown()
    assert result.observations[0]["vuln_category"] == "XSS"


def test_rate_limit_check() -> None:
    server, base = _server()
    try:
        result = _run(base, "rate_limit")
    finally:
        server.shutdown()
    assert result.observations[0]["vuln_category"] == "AuthenticationExposure"


def test_security_headers_check() -> None:
    server, base = _server()
    try:
        result = _run(base, "security_headers")
    finally:
        server.shutdown()
    categories = {
        observation.get("vuln_category")
        for observation in result.observations
    }
    assert "HeaderSecurity" in categories
    assert "CookieSecurity" in categories


def test_security_headers_uses_custom_login_path() -> None:
    server, base = _server()
    try:
        result = _run(
            base,
            "security_headers",
            {"login_path": "/wp-login.php"},
        )
    finally:
        server.shutdown()
    for observation in result.observations:
        assert "/wp-login.php" in str(observation["endpoint"])


def test_csrf_replay_proof_uses_custom_path() -> None:
    server, base = _server()
    try:
        result = _run(
            base,
            "csrf",
            {"csrf_path": "/custom-csrf/"},
        )
    finally:
        server.shutdown()
    assert result.observations[0]["vuln_category"] == "CSRF"
    assert (
        result.observations[0]["replay_proof"]["endpoint"]
        == "/custom-csrf/"
    )
