from __future__ import annotations

from services.control_plane.app.contracts import AgentEvent
from services.control_plane.app.redactor import Redactor


def test_redactor_hides_secret_text_and_headers() -> None:
    redactor = Redactor()

    text = redactor.redact_text('{"token": "secret-value", "user": "alice"}')
    headers = redactor.redact_headers(
        {
            "Cookie": "session=abc",
            "User-Agent": "fixture",
            "Authorization": "Bearer key",
        }
    )

    assert "secret-value" not in text
    assert "user" in text
    assert headers["Cookie"] == "[REDACTED:cookie]"
    assert headers["Authorization"] == "[REDACTED:authorization]"
    assert headers["User-Agent"] == "fixture"


def test_redactor_event_payload_recursively_without_leak() -> None:
    redactor = Redactor()
    event = AgentEvent(
        event_id="evt_secret",
        event_type="observation.ingested",
        stream_id="run_1",
        run_id="run_1",
        actor="agent-worker",
        payload={
            "tool": "shell.probe",
            "output": '{"api_key": "sk-live-123"}',
            "nested": {"token": "abc", "ok": "visible"},
            "list": ["keep", 'password=horse-battery'],
        },
    )

    redacted = redactor.redact_event(event)
    blob = redacted.model_dump_json()

    assert event.event_id == redacted.event_id
    assert event.actor == redacted.actor
    assert "sk-live-123" not in blob
    assert "abc" not in blob
    assert "horse-battery" not in blob
    assert "visible" in blob
    assert "keep" in blob
    assert redacted.payload["nested"]["token"] == "[REDACTED:token]"
