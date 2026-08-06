from __future__ import annotations

from services.research_service.graph_benchmark import compare_single_vs_graph
from services.research_service.models import Scenario


def test_compare_single_vs_graph_reports_delta_and_recommendation() -> None:
    scenario = Scenario(
        scenario_id="web-idor-001",
        name="Web IDOR role mutation",
        target_ref="https://lab.example.test",
        mode="single",
    )

    single, graph, delta, recommendation = compare_single_vs_graph(
        scenario,
        runs=2,
    )

    assert single.aggregate["verified_avg"] >= 0
    assert graph.aggregate["verified_avg"] >= single.aggregate["verified_avg"]
    assert delta["verified_avg"]["delta"] >= 0
    assert recommendation in ("single", "graph")
    assert delta["cost_avg"]["delta"] == 0
