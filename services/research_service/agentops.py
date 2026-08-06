from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from services.control_plane.app.contracts import AgentEvent

from .models import BehaviorSnapshot, Trajectory
from .trajectory import compute_metrics


def export_trajectory(
    events: list[AgentEvent],
    *,
    run_id: str,
    out_path: str | Path,
) -> dict[str, Any]:
    metrics = compute_metrics(events)
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "metrics": metrics,
        "events": [event.model_dump(mode="json") for event in events],
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return {
        "path": str(out),
        "run_id": run_id,
        "event_count": len(events),
    }


def harness_loop_graph_metrics(events: list[AgentEvent]) -> dict[str, Any]:
    node_ids: set[str] = set()
    loop_iterations = 0
    harness_snapshots = 0
    for event in events:
        node_id = event.payload.get("node_id")
        if node_id:
            node_ids.add(str(node_id))
        if event.event_type in ("model.turn.started", "loop.started"):
            loop_iterations += 1
        if event.payload.get("harness_digest") or event.payload.get(
            "behavior_snapshot"
        ):
            harness_snapshots += 1
    return {
        "loop_iterations": loop_iterations,
        "graph_nodes": len(node_ids),
        "harness_snapshots": harness_snapshots,
    }


def aggregate_trajectories(trajectories: list[Trajectory]) -> dict[str, float]:
    if not trajectories:
        return {}
    runs = len(trajectories)
    return {
        "completion_rate": round(
            sum(trajectory.metrics.get("loop_completion", 0.0) for trajectory in trajectories)
            / runs,
            3,
        ),
        "verified_avg": round(
            sum(
                trajectory.metrics.get("verified_result_rate", 0.0)
                for trajectory in trajectories
            )
            / runs,
            3,
        ),
        "verified_runs_rate": round(
            sum(
                1
                for trajectory in trajectories
                if trajectory.metrics.get("verified_count", 0) > 0
            )
            / runs,
            3,
        ),
        "duplicate_actions_avg": round(
            sum(
                trajectory.metrics.get("duplicate_actions", 0.0)
                for trajectory in trajectories
            )
            / runs,
            3,
        ),
        "cost_avg": round(
            sum(trajectory.metrics.get("cost_estimate", 0.0) for trajectory in trajectories)
            / runs,
            3,
        ),
    }


@dataclass(frozen=True)
class RegressionMetric:
    name: str
    baseline: float
    current: float
    delta: float
    regressed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentOpsRegression:
    def __init__(
        self,
        baseline_aggregate: dict[str, Any],
        baseline_snapshot: BehaviorSnapshot,
    ) -> None:
        self._baseline_aggregate = baseline_aggregate
        self._baseline_snapshot = baseline_snapshot

    def evaluate(
        self,
        trajectories: list[Trajectory],
        current_snapshot: BehaviorSnapshot,
    ) -> dict[str, Any]:
        aggregate = aggregate_trajectories(trajectories)
        metrics: list[RegressionMetric] = []
        for name, value in self._baseline_aggregate.items():
            baseline = float(value)
            current = float(aggregate.get(name, 0.0))
            higher_is_better = name not in (
                "cost_avg",
                "duplicate_actions_avg",
                "p95_ms",
            )
            delta = round(current - baseline, 3)
            regressed = (
                current < baseline if higher_is_better else current > baseline
            )
            metrics.append(
                RegressionMetric(
                    name=name,
                    baseline=baseline,
                    current=current,
                    delta=delta,
                    regressed=regressed,
                )
            )
        snapshot_diff = self._baseline_snapshot.diff(current_snapshot)
        regressed = any(metric.regressed for metric in metrics) or bool(snapshot_diff)
        return {
            "aggregate": aggregate,
            "metrics": [metric.to_dict() for metric in metrics],
            "snapshot_diff": snapshot_diff,
            "regressed": regressed,
            "trajectory_count": len(trajectories),
        }
