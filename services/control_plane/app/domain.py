from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Project(BaseModel):
    project_id: str
    name: str
    owner: str = "local"
    created_at: str = Field(default_factory=utc_now)


class TargetProfile(BaseModel):
    target_id: str
    project_id: str
    url: str
    allowed: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    authorization: str = "authorized"
    created_at: str = Field(default_factory=utc_now)


class Mission(BaseModel):
    mission_id: str
    project_id: str
    name: str
    spec: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class RunState(BaseModel):
    run_id: str
    mission_id: str
    source_run_id: str | None = None
    status: str
    event_count: int
    observations: list[dict] = Field(default_factory=list)
    stop_reason: str | None = None
    created_at: str


class RunTransition(BaseModel):
    run_id: str
    status: str
    reason: str | None = None


class PolicyDecision(BaseModel):
    allowed: bool
    risk_level: str
    rule: str
    explanation: str = ""


class ApprovalRequest(BaseModel):
    approval_id: str
    run_id: str
    tool_ref: str
    risk_level: str
    state: str
    policy_rule: str
    reason: str = ""
    requested_at: str = Field(default_factory=utc_now)
    decided_at: str | None = None
    decided_by: str | None = None
    budget_reserved: int = 1


class LeaseRecord(BaseModel):
    worker_id: str
    lease_until: str
    last_seen_at: str
