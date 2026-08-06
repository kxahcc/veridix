from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AgentEvent(BaseModel):
    schema_version: int = 1
    event_id: str
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    stream_id: str
    run_id: str
    actor: str
    occurred_at: str = Field(default_factory=utc_now)
    sequence: int | None = None
    payload: dict = Field(default_factory=dict)


class CommandEnvelope(BaseModel):
    schema_version: int = 1
    command_id: str
    command_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    run_id: str
    idempotency_key: str
    requested_at: str = Field(default_factory=utc_now)
    payload: dict = Field(default_factory=dict)


class CommandRecord(BaseModel):
    command_id: str
    command_type: str
    run_id: str
    idempotency_key: str
    state: str
    requested_at: str
    accepted_at: str | None = None
    rejected_reason: str | None = None
    payload: dict = Field(default_factory=dict)
