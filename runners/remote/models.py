from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RemoteNode:
    node_id: str
    version: str
    capabilities: tuple[str, ...]
    public_key: str
    status: str = "offline"
    last_seen_at: str | None = None
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class NodeRegistration:
    node_id: str
    version: str
    capabilities: tuple[str, ...]
    public_key: str


@dataclass(frozen=True)
class TaskLease:
    lease_id: str
    node_id: str
    task_ref: str
    expires_at: str
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class NodeResult:
    result_id: str
    node_id: str
    task_ref: str
    status: str
    artifact_refs: tuple[str, ...] = ()
    signature: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CallbackRecord:
    callback_id: str
    token: str
    source: str
    observed_at: str = field(default_factory=utc_now)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OastToken:
    token: str
    source: str
    purpose: str
    expires_at: str
    issued_at: str
    used: bool = False
