from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from services.control_plane.app.contracts import AgentEvent

from .agentops import AgentOpsRegression, export_trajectory, harness_loop_graph_metrics
from .models import BehaviorSnapshot, Trajectory
from .trajectory import compute_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="run the AgentOps regression")
    parser.add_argument("--input", required=True, help="JSON regression input")
    parser.add_argument("--out", default=None, help="optional report path")
    args = parser.parse_args()

    config = json.loads(Path(args.input).read_text(encoding="utf-8"))
    baseline_snapshot = BehaviorSnapshot(**config["baseline_snapshot"])
    current_snapshot = BehaviorSnapshot(**config["current_snapshot"])
    trajectories: list[Trajectory] = []
    exports: list[dict[str, Any]] = []
    for item in config.get("runs", []):
        events = [AgentEvent(**event) for event in item.get("events", [])]
        trajectory = Trajectory(
            scenario_id=item.get("scenario_id", "unknown"),
            run_id=item["run_id"],
            events=tuple(events),
            metrics=compute_metrics(events),
        )
        trajectories.append(trajectory)
        if args.out:
            exports.append(
                export_trajectory(
                    events,
                    run_id=trajectory.run_id,
                    out_path=Path(args.out).parent / f"{trajectory.run_id}.json",
                )
            )

    report = AgentOpsRegression(
        config.get("baseline_aggregate", {}),
        baseline_snapshot,
    ).evaluate(trajectories, current_snapshot)
    report["harness_loop_graph"] = [
        {
            "run_id": trajectory.run_id,
            **harness_loop_graph_metrics(list(trajectory.events)),
        }
        for trajectory in trajectories
    ]
    report["exports"] = exports
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(report, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
