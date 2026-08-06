from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from services.control_plane.app.contracts import AgentEvent


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    name: str
    target_ref: str
    expected_findings: tuple[str, ...] = ()
    max_turns: int = 5
    mode: str = "single"


@dataclass(frozen=True)
class Trajectory:
    scenario_id: str
    run_id: str
    events: tuple[AgentEvent, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkResult:
    scenario_id: str
    mode: str
    runs: int
    trajectories: tuple[Trajectory, ...]
    aggregate: dict[str, Any]


@dataclass(frozen=True)
class BehaviorSnapshot:
    snapshot_id: str
    config_hash: str
    harness_digest: str
    provider: str
    created_at: str = field(default_factory=utc_now)

    def diff(self, other: BehaviorSnapshot) -> list[str]:
        changed = []
        for field_name in ("config_hash", "harness_digest", "provider"):
            if getattr(self, field_name) != getattr(other, field_name):
                changed.append(field_name)
        return changed
