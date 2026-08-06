from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from services.agent_runtime.kernel.contracts import ExecutionRequest
from services.agent_runtime.kernel.web_tool_runner import WebToolRunner
from runners.web.proxy_gateway import RequestStore


ROOT = Path(__file__).resolve().parents[2]


class WebFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"page": "web-fixture", "secret": "value"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


def test_request_store_load_missing_file_returns_empty(tmp_path) -> None:
    store = RequestStore.load(str(tmp_path / "missing.jsonl"))

    assert store.records() == ()


@pytest.mark.integration
def test_web_tool_runner_captures_through_browser_and_proxy() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), WebFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runner = WebToolRunner(str(ROOT))
    try:
        result = runner.execute(
            ExecutionRequest(
                action_id="action_web_1",
                run_id="run_web_1",
                tool_ref="browser.open",
                input={"url": f"http://127.0.0.1:{server.server_port}/"},
                idempotency_key="run_web_1:browser.open:1",
            )
        )

        assert result.status == "completed"
        assert result.exit_code == 0
        assert "captured" in result.stdout

        listed = runner.execute(
            ExecutionRequest(
                action_id="action_web_2",
                run_id="run_web_1",
                tool_ref="proxy.list",
                input={"endpoint": "/"},
                idempotency_key="run_web_1:proxy.list:1",
            )
        )
        assert f"/" in listed.stdout
        assert "secret" not in listed.stdout
    finally:
        runner.close()
        server.shutdown()
        thread.join(timeout=5)
