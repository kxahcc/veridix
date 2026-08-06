from __future__ import annotations

import json
import subprocess
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from services.agent_runtime.golden import GoldenRunDriver, GoldenRunSpec


class GoldenOpenAIHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:
        if not self.path.endswith("/chat/completions"):
            self._send(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).calls += 1
        if type(self).calls == 1:
            payload = {
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "created": 1,
                "model": body.get("model", "fixture-model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "shell.probe",
                                        "arguments": json.dumps(
                                            {"target": "https://lab.example.test"}
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        else:
            payload = {
                "id": "chatcmpl_2",
                "object": "chat.completion",
                "created": 2,
                "model": body.get("model", "fixture-model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "probe complete",
                        },
                    }
                ],
            }
        self._send(200, payload)

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


def make_server():
    GoldenOpenAIHandler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), GoldenOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/v1", thread


def test_golden_run_driver_success_and_evidence() -> None:
    server, endpoint, thread = make_server()
    try:
        spec = GoldenRunSpec(
            run_id="golden_1",
            mission="find an exposed admin panel",
            target_ref="https://lab.example.test",
            behavior_snapshot="behavior_golden_1",
            provider_endpoint=endpoint,
            provider_model="fixture-model",
        )

        result = GoldenRunDriver(timeout_seconds=30).run(spec)

        assert result.status == "succeeded"
        assert result.oracle_passed is True
        assert result.finding is not None
        assert result.finding.status.value == "verified"
        assert result.metrics["loop_completion"] == 1.0
        assert result.evidence_refs
        assert len(result.harness_digest) == 64
        assert result.behavior_snapshot_id == "behavior_golden_1"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_golden_run_driver_provider_failure_returns_failed() -> None:
    spec = GoldenRunSpec(
        run_id="golden_fail",
        mission="probe",
        target_ref="https://lab.example.test",
        behavior_snapshot="behavior_golden_fail",
        provider_endpoint="http://127.0.0.1:1/v1",
        provider_model="fixture-model",
    )

    result = GoldenRunDriver(timeout_seconds=1).run(spec)

    assert result.status == "failed"
    assert result.oracle_passed is False


def test_golden_run_driver_passes_request_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs) -> object:
            captured.update(kwargs)

            class FakeMessage:
                content = "done"
                tool_calls = None

            class FakeChoice:
                message = FakeMessage()

            class FakeResponse:
                choices = [FakeChoice()]

            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            pass

        chat = FakeChat()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    spec = GoldenRunSpec(
        run_id="golden_options",
        mission="probe",
        target_ref="https://lab.example.test",
        behavior_snapshot="behavior_options",
        provider_endpoint="http://127.0.0.1:1/v1",
        provider_model="fixture-model",
        thinking_mode="enabled",
        tool_choice="auto",
    )

    result = GoldenRunDriver().run(spec)

    assert result.status == "succeeded"
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert captured["tool_choice"] == "auto"


def test_golden_cli_outputs_json() -> None:
    server, endpoint, thread = make_server()
    root = Path(__file__).resolve().parents[2]
    try:
        completed = None
        for attempt in range(2):
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "services.agent_runtime.golden_cli",
                        "--run-id",
                        "golden_cli_1",
                        "--mission",
                        "find an exposed admin panel",
                        "--target",
                        "https://lab.example.test",
                        "--behavior",
                        "behavior_cli_1",
                        "--endpoint",
                        endpoint,
                        "--model",
                        "fixture-model",
                    ],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                break
            except subprocess.TimeoutExpired:
                if attempt == 1:
                    raise
                time.sleep(2)

        assert completed.returncode == 0
        payload = json.loads(completed.stdout)
        assert payload["status"] == "succeeded"
        assert payload["oracle_passed"] is True
        assert payload["harness_digest"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_golden_cli_dry_run_validates_inputs() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.agent_runtime.golden_cli",
            "--run-id",
            "golden_dry",
            "--mission",
            "probe",
            "--target",
            "https://lab.example.test",
            "--behavior",
            "behavior_dry",
            "--endpoint",
            "http://127.0.0.1:9999/v1",
            "--model",
            "fixture-model",
            "--thinking-mode",
            "enabled",
            "--tool-choice",
            "auto",
            "--dry-run",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["dry_run"] is True
    assert payload["ready"] is True
    assert payload["thinking_mode"] == "enabled"
    assert payload["tool_choice"] == "auto"
