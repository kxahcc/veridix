from __future__ import annotations

import json

from runners.web.proxy_gateway import ProxyCaptureAddon


class FakeHeaders(dict):
    def __init__(self, items: dict) -> None:
        super().__init__(items)


class FakeMessage:
    def __init__(self, content: bytes, frame_type: str = "text") -> None:
        self.content = content
        self.type = frame_type


class FakeRequest:
    def __init__(self) -> None:
        self.pretty_url = "wss://lab.example.test/events"
        self.headers = FakeHeaders({"Authorization": "Bearer tok"})


class FakeWebsocket:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages


class FakeFlow:
    def __init__(self) -> None:
        self.request = FakeRequest()
        self.websocket = FakeWebsocket(
            [
                FakeMessage(b'{"type": "ping", "ts": 1}', "text"),
                FakeMessage(b"\x01\x02", "binary"),
            ]
        )


def test_websocket_message_capture_writes_observations(tmp_path) -> None:
    out = tmp_path / "capture.jsonl"
    addon = ProxyCaptureAddon(
        str(out),
        web_session_id="ws_1",
        proxy_session_id="proxy_1",
    )

    addon.websocket_message(FakeFlow())

    rows = [
        json.loads(line)
        for line in out.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 2
    assert rows[0]["protocol"] == "websocket"
    assert rows[0]["endpoint"] == "WSS lab.example.test/events"
    assert rows[0]["ws_frame_type"] == "text"
    assert rows[0]["ws_frame_data"] == '{"type": "ping", "ts": 1}'
    assert rows[1]["ws_frame_type"] == "binary"
    assert addon._store.records()[0].protocol == "websocket"
