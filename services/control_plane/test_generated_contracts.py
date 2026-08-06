from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from services.control_plane.app.generated_contracts import (
    AgentEventEnvelope,
    CommandEnvelope,
    InferenceProfile,
)


def test_generated_pydantic_contracts_validate() -> None:
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        ["python", "packages/contracts/scripts/generate_types.py"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )

    event = AgentEventEnvelope(
        schemaVersion=1,
        eventId="evt_1",
        eventType="run.started",
        streamId="run_1",
        runId="run_1",
        actor="control",
        occurredAt="2026-08-01T00:00:00Z",
        payload={"mission_id": "m1"},
    )
    command = CommandEnvelope(
        schemaVersion=1,
        commandId="cmd_1",
        commandType="run.start",
        runId="run_1",
        idempotencyKey="key",
        requestedAt="2026-08-01T00:00:00Z",
        payload={},
    )

    assert event.eventType == "run.started"
    assert event.sequence is None
    assert command.commandType == "run.start"

    profile = InferenceProfile(
        providerId="deepseek",
        model="deepseek-v4-flash",
        endpoint="https://api.deepseek.com",
        dataPolicy="local",
        timeoutSeconds=30,
        requestOptions={
            "thinkingMode": "enabled",
            "toolChoice": "auto",
        },
    )
    assert profile.requestOptions == {
        "thinkingMode": "enabled",
        "toolChoice": "auto",
    }
