from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_add(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


class ResourceStatus(str, Enum):
    CREATED = "created"
    LAUNCHING = "launching"
    READY = "ready"
    ACTIVE = "active"
    DETACHED = "detached"
    STALE = "stale"
    LOST = "lost"
    CLOSED = "closed"


@dataclass
class ResourceHandle:
    resource_id: str
    kind: str
    status: ResourceStatus = ResourceStatus.CREATED
    lease_until: str | None = None
    last_seen_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)

    def record(self, note: str) -> None:
        self.history.append(f"{utc_now()} {note}")


class ResourceManager:
    def __init__(
        self,
        *,
        stale_after_seconds: int = 60,
        lost_after_seconds: int = 300,
        lease_seconds: int = 300,
    ) -> None:
        self._stale_after = stale_after_seconds
        self._lost_after = lost_after_seconds
        self._lease_seconds = lease_seconds
        self._handles: dict[str, ResourceHandle] = {}

    @property
    def count(self) -> int:
        return len(self._handles)

    def create(self, resource_id: str, kind: str) -> ResourceHandle:
        handle = ResourceHandle(resource_id=resource_id, kind=kind)
        handle.record("created")
        self._handles[resource_id] = handle
        return handle

    def get(self, resource_id: str) -> ResourceHandle:
        return self._handles[resource_id]

    def mark_ready(self, resource_id: str) -> ResourceHandle:
        handle = self.get(resource_id)
        handle.last_seen_at = utc_now()
        return self._transition(handle, ResourceStatus.READY, "ready")

    def attach(self, resource_id: str) -> ResourceHandle:
        handle = self.get(resource_id)
        if handle.status == ResourceStatus.STALE:
            handle.status = ResourceStatus.READY
            handle.record("reconnected")
        if handle.status not in (ResourceStatus.READY, ResourceStatus.DETACHED):
            raise ValueError(
                f"invalid transition from {handle.status.value} for {resource_id}"
            )
        handle.lease_until = utc_add(self._lease_seconds)
        handle.last_seen_at = utc_now()
        return self._transition(handle, ResourceStatus.ACTIVE, "attached")

    def detach(self, resource_id: str) -> ResourceHandle:
        handle = self.get(resource_id)
        if handle.status != ResourceStatus.ACTIVE:
            raise ValueError("only active resources can detach")
        return self._transition(handle, ResourceStatus.DETACHED, "detached")

    def heartbeat(self, resource_id: str) -> ResourceHandle:
        handle = self.get(resource_id)
        handle.last_seen_at = utc_now()
        handle.record("heartbeat")
        return handle

    def reconcile(self, *, now: str | None = None) -> list[ResourceHandle]:
        now = now or utc_now()
        changed: list[ResourceHandle] = []
        for handle in self._handles.values():
            if handle.last_seen_at is None or handle.status not in (
                ResourceStatus.ACTIVE,
                ResourceStatus.READY,
                ResourceStatus.STALE,
            ):
                continue
            age = _iso_age_seconds(handle.last_seen_at, now)
            if handle.status == ResourceStatus.STALE and age >= self._lost_after:
                changed.append(
                    self._transition(handle, ResourceStatus.LOST, "heartbeat_lost")
                )
            elif age >= self._stale_after:
                changed.append(
                    self._transition(handle, ResourceStatus.STALE, "heartbeat_stale")
                )
        return changed

    def close(self, resource_id: str) -> ResourceHandle:
        return self._transition(self.get(resource_id), ResourceStatus.CLOSED, "closed")

    def destroy(self, resource_id: str) -> None:
        self._handles.pop(resource_id, None)

    def _transition(
        self,
        handle: ResourceHandle,
        status: ResourceStatus,
        note: str,
    ) -> ResourceHandle:
        handle.status = status
        handle.record(note)
        return handle


def _iso_age_seconds(start: str, end: str) -> int:
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return int((end_dt - start_dt).total_seconds())
