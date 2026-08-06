from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BaselineComparison:
    metric: str
    baseline: float
    actual: float
    delta: float
    meets: bool


def load_baseline(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_to_baseline(aggregate: dict, baseline: dict) -> list[BaselineComparison]:
    comparisons = []
    for metric, value in baseline.items():
        actual = float(aggregate.get(metric, 0.0))
        target = float(value)
        delta = round(actual - target, 3)
        higher_is_better = metric not in ("cost_avg", "duplicate_actions_avg", "p95_ms")
        meets = actual >= target if higher_is_better else actual <= target
        comparisons.append(
            BaselineComparison(
                metric=metric,
                baseline=target,
                actual=actual,
                delta=delta,
                meets=meets,
            )
        )
    return comparisons


def meets_baseline(comparisons: list[BaselineComparison]) -> bool:
    return all(item.meets for item in comparisons)
