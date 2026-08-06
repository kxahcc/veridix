from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from fastapi.testclient import TestClient

from services.control_plane.app.main import create_app


def make_server(mode: str = "ok"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.endswith("/models"):
                self._send(
                    200,
                    {
                        "data": [
                            {"id": "fixture-model", "object": "model"},
                        ]
                    },
                )
                return
            self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path.endswith("/chat/completions"):
                if self.mode == "chat_down":
                    self._send(500, {"error": "unavailable"})
                    return
                self._send(
                    200,
                    {
                        "id": "chatcmpl-fixture",
                        "object": "chat.completion",
                        "created": 0,
                        "model": "fixture-model",
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {
                                    "role": "assistant",
                                    "content": "pong",
                                },
                            }
                        ],
                    },
                )
                return
            if self.path.endswith("/embeddings"):
                if self.mode == "no_embeddings":
                    self._send(500, {"error": "unavailable"})
                    return
                self._send(200, {"data": [{"embedding": [0.1, 0.2, 0.3]}]})
                return
            if self.path.endswith("/rerank"):
                self._send(200, {"results": [{"index": 0}, {"index": 1}]})
                return
            self._send(404, {"error": "not_found"})

        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:  # pragma: no cover - test noise
            return

    Handler.mode = mode
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    deadline = time.time() + 15.0
    while time.time() < deadline:
        try:
            response = httpx.get(f"{endpoint}/models", timeout=2.0)
            if response.status_code == 200:
                return server, endpoint, thread
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("provider probe mock server did not become ready")


def test_provider_probe_success_and_rag_degraded_event() -> None:
    ok_server, ok_url, ok_thread = make_server("ok")
    try:
        with TestClient(create_app(":memory:")) as client:
            run = client.post(
                "/api/v1/projects", json={"name": "probe"}
            ).json()
            mission = client.post(
                "/api/v1/missions",
                json={
                    "project_id": run["project_id"],
                    "name": "probe",
                    "spec": {},
                },
            ).json()
            run = client.post(
                f"/api/v1/missions/{mission['mission_id']}/runs",
                json={
                    "mission_id": mission["mission_id"],
                    "idempotency_key": "probe:start",
                },
            ).json()

            ok = client.post(
                "/api/v1/providers/probe",
                json={
                    "provider_id": "fixture",
                    "endpoint": ok_url,
                    "model": "fixture-model",
                },
            ).json()
            assert ok["status"] == "ok"
            assert ok["capabilities"]["dimensions"] == 3
            chat_only_server, chat_only_url, chat_only_thread = make_server(
                "no_embeddings"
            )
            down_server, down_url, down_thread = make_server("chat_down")
            try:
                chat_only = client.post(
                    "/api/v1/providers/probe",
                    json={
                        "provider_id": "chat-only",
                        "endpoint": chat_only_url,
                        "model": "fixture-model",
                    },
                ).json()
                assert chat_only["status"] == "ok"
                assert chat_only["capabilities"]["embeddings"] is False

                degraded = client.post(
                    "/api/v1/providers/probe",
                    json={
                        "provider_id": "fixture",
                        "endpoint": down_url,
                        "model": "fixture-model",
                        "run_id": run["run_id"],
                    },
                ).json()
                assert degraded["status"] == "degraded"
                assert degraded["event_type"] == "rag_degraded"
                assert "rag_degraded" in degraded["reason"]

                events = client.get(
                    f"/api/v1/runs/{run['run_id']}/events", params={"after": 0}
                ).json()
                assert any(
                    event["event_type"] == "rag_degraded" for event in events
                )
            finally:
                chat_only_server.shutdown()
                chat_only_thread.join(timeout=2)
                down_server.shutdown()
                down_thread.join(timeout=2)
    finally:
        ok_server.shutdown()
        ok_thread.join(timeout=2)


def test_set_provider_default_preserves_existing_config() -> None:
    with TestClient(create_app(":memory:")) as client:
        registered = client.post(
            "/api/v1/runtime/providers",
            json={
                "provider_id": "deepseek",
                "model": "deepseek-chat",
                "endpoint": "https://api.deepseek.com/v1",
                "status": "ok",
                "api_key_ref": "env:DEEPSEEK_API_KEY",
                "backend": "openai",
                "timeout_seconds": 15,
                "retries": 5,
            },
        ).json()
        default = client.post(
            "/api/v1/settings/provider-default",
            json={
                "provider_id": "deepseek",
                "endpoint": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "api_key_ref": "env:DEEPSEEK_API_KEY",
            },
        ).json()
        providers = client.get("/api/v1/runtime/providers").json()
        row = next(
            item for item in providers if item["provider_id"] == "deepseek"
        )
        assert default["provider_id"] == "deepseek"
        assert row["config"]["api_key_ref"] == "env:DEEPSEEK_API_KEY"
        assert row["config"]["timeout_seconds"] == 15
        assert registered["provider_id"] == "deepseek"
