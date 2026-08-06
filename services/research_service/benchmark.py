from __future__ import annotations

from typing import Callable

from .models import BenchmarkResult, Scenario, Trajectory
from .trajectory import compute_metrics


class BenchmarkRunner:
    def __init__(self, runner: Callable[[Scenario], list]) -> None:
        self._runner = runner

    def run(self, scenario: Scenario, *, runs: int = 3) -> BenchmarkResult:
        trajectories = []
        for index in range(runs):
            events = self._runner(scenario)
            trajectories.append(
                Trajectory(
                    scenario_id=scenario.scenario_id,
                    run_id=f"{scenario.scenario_id}_{index}",
                    events=tuple(events),
                    metrics=compute_metrics(events),
                )
            )
        aggregate = {
            "completion_rate": round(
                sum(trajectory.metrics["loop_completion"] for trajectory in trajectories)
                / runs,
                3,
            ),
            "verified_avg": round(
                sum(
                    trajectory.metrics["verified_result_rate"]
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
                    trajectory.metrics["duplicate_actions"]
                    for trajectory in trajectories
                )
                / runs,
                3,
            ),
            "cost_avg": round(
                sum(trajectory.metrics["cost_estimate"] for trajectory in trajectories)
                / runs,
                3,
            ),
        }
        return BenchmarkResult(
            scenario_id=scenario.scenario_id,
            mode=scenario.mode,
            runs=runs,
            trajectories=tuple(trajectories),
            aggregate=aggregate,
        )

    @staticmethod
    def compare(single: BenchmarkResult, graph: BenchmarkResult) -> dict:
        keys = (
            "completion_rate",
            "verified_avg",
            "duplicate_actions_avg",
            "cost_avg",
        )
        return {
            key: {
                "single": single.aggregate.get(key),
                "graph": graph.aggregate.get(key),
                "delta": round(graph.aggregate.get(key, 0) - single.aggregate.get(key, 0), 3),
            }
            for key in keys
        }
