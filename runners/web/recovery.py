from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from runners.container.resource_handle import ResourceHandle, ResourceStatus, utc_now


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason: str


@dataclass(frozen=True)
class RecoveryRecord:
    resource_id: str
    resource_type: str
    action: str
    reason: str
    from_status: str | None = None
    new_resource_id: str | None = None
    reobserve_required: bool = False
    run_id: str | None = None
    occurred_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_run_event(self, stream_id: str):
        from services.control_plane.app.contracts import AgentEvent

        return AgentEvent(
            event_id=f"resource.recovered:{self.resource_id}:{uuid4().hex[:8]}",
            event_type="resource.recovered",
            stream_id=stream_id,
            run_id=self.run_id or stream_id,
            actor="runner",
            occurred_at=self.occurred_at,
            payload={
                "resource_id": self.resource_id,
                "resource_type": self.resource_type,
                "action": self.action,
                "reason": self.reason,
                "from_status": self.from_status,
                "new_resource_id": self.new_resource_id,
                "reobserve_required": self.reobserve_required,
            },
        )


class RecoveryLog:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._records: list[RecoveryRecord] = []

    def append(
        self,
        record: RecoveryRecord,
        *,
        persist: bool = True,
    ) -> RecoveryRecord:
        self._records.append(record)
        if persist and self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict(), ensure_ascii=True) + "\n")
        return record

    def records(self) -> tuple[RecoveryRecord, ...]:
        return tuple(self._records)

    def to_run_events(self, run_id: str):
        return [record.to_run_event(run_id) for record in self._records]

    @classmethod
    def load(cls, path: str | Path) -> "RecoveryLog":
        log = cls(path)
        with Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    log.append(RecoveryRecord(**json.loads(line)), persist=False)
        return log


def decide_recovery(
    handle: ResourceHandle,
    *,
    reconnect_capability: bool,
) -> RecoveryDecision:
    if handle.status in (ResourceStatus.ACTIVE, ResourceStatus.READY):
        return RecoveryDecision("reuse", "resource_verified")
    if handle.status == ResourceStatus.DETACHED and reconnect_capability:
        return RecoveryDecision("reconnect", "detached_with_reconnect")
    if handle.status == ResourceStatus.STALE:
        return RecoveryDecision("revalidate", "heartbeat_stale")
    if handle.status in (ResourceStatus.LOST, ResourceStatus.CLOSED):
        return RecoveryDecision("rebuild", "resource_lost")
    return RecoveryDecision("unavailable", "resource_unknown")
