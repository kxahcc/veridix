from __future__ import annotations

from services.research_service.baseline import (
    compare_to_baseline,
    load_baseline,
    meets_baseline,
)


def test_baseline_compare_against_strix() -> None:
    baseline = load_baseline("benchmarks/baselines/strix-web-idor.json")
    aggregate = {
        "completion_rate": 0.95,
        "verified_runs_rate": 0.95,
        "duplicate_actions_avg": 0.4,
        "cost_avg": 0.9,
    }

    comparisons = compare_to_baseline(aggregate, baseline)

    assert meets_baseline(comparisons) is True
    assert comparisons[0].delta == 0.05
    verified_runs = next(
        item for item in comparisons if item.metric == "verified_runs_rate"
    )
    assert verified_runs.delta == 0.05
    cost = next(item for item in comparisons if item.metric == "cost_avg")
    assert cost.delta == -0.1


def test_baseline_fails_when_metrics_miss() -> None:
    baseline = {"completion_rate": 1.0, "cost_avg": 0.5}
    aggregate = {"completion_rate": 0.8, "cost_avg": 0.7}

    comparisons = compare_to_baseline(aggregate, baseline)

    assert meets_baseline(comparisons) is False
