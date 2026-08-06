from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.agent_runtime.kernel.contracts import ExecutionRequest
from runners.web.ssrf_runner import SSRFTesterRunner


class CallbackHandler(BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:
        type(self).hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:  # pragma: no cover
        return


def test_ssrf_runner_fetches_callback() -> None:
    CallbackHandler.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        callback_url = (
            f"http://127.0.0.1:{server.server_port}/callback/oast_gate"
        )
        runner = SSRFTesterRunner(timeout=5)
        request = ExecutionRequest(
            action_id="action_ssrf",
            run_id="run_ssrf",
            tool_ref="web.ssrf.test",
            input={"callback_url": callback_url},
            idempotency_key="run_ssrf:1",
        )

        result = runner.execute(request)

        assert result.status == "completed"
        assert result.observations[0]["kind"] == "ssrf_fetch"
        assert result.observations[0]["status"] == 200
        assert CallbackHandler.hits == ["/callback/oast_gate"]
    finally:
        server.shutdown()
        thread.join(timeout=2)
