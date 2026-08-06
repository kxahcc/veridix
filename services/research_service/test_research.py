from __future__ import annotations

from services.control_plane.app.contracts import AgentEvent
from services.research_service.behaviors import (
    snapshot_from_components,
    snapshot_from_harness,
)
from services.research_service.benchmark import BenchmarkRunner
from services.research_service.models import Scenario
from services.research_service.oracle import ScenarioOracle
from services.research_service.scenarios import load_scenario
from services.research_service.trajectory import compute_metrics


def make_event(event_type: str, payload: dict | None = None, seq: int = 1) -> AgentEvent:
    return AgentEvent(
        event_id=f"evt_{seq}",
        event_type=event_type,
        stream_id="run_1",
        run_id="run_1",
        actor="agent-worker",
        sequence=seq,
        payload=payload or {},
    )


def test_scenario_load_and_oracle() -> None:
    scenario = load_scenario("benchmarks/scenarios/web-idor.json")
    oracle = ScenarioOracle()

    assert scenario.scenario_id == "web-idor-001"
    assert scenario.expected_findings == ("finding_authz_admin",)
    events = [
        make_event("run.started", seq=1),
        make_event("finding.verified", {"finding_id": "finding_authz_admin"}, seq=2),
        make_event("run.succeeded", seq=3),
    ]
    assert oracle.check(scenario, events) is True
    assert oracle.check(scenario, [make_event("run.started", seq=1)]) is False


def test_trajectory_metrics() -> None:
    metrics = compute_metrics(
        [
            make_event("run.started", seq=1),
            make_event("tool.completed", {"tool": "shell.probe", "cost_estimate": 0.5}, seq=2),
            make_event("tool.completed", {"tool": "shell.probe", "cost_estimate": 0.5}, seq=3),
            make_event("tool.failed", {"tool": "shell.exec", "exit_code": 1}, seq=4),
            make_event("model.retry", {"attempt": 1}, seq=5),
            make_event("run.cancelled", seq=6),
            make_event("run.succeeded", seq=7),
        ]
    )

    assert metrics["loop_completion"] == 1.0
    assert metrics["duplicate_actions"] == 1
    assert metrics["cost_estimate"] == 1.0
    assert metrics["tool_errors"] == 1
    assert metrics["cancelled"] == 1
    assert metrics["retries"] == 1


def test_trajectory_mature_metrics() -> None:
    metrics = compute_metrics(
        [
            make_event("run.started", seq=1),
            make_event(
                "loop.replan.suggested",
                {"reason": "no_progress"},
                seq=2,
            ),
            make_event(
                "tool.completed",
                {"tool": "shell.probe"},
                seq=3,
            ),
            make_event(
                "finding.verified",
                {"finding_id": "f1", "evidence_refs": ["e1", "e2"]},
                seq=4,
            ),
            make_event(
                "graph.completed",
                {
                    "node_count": 3,
                    "dead_letters": 0,
                    "handoff_loss": 1,
                    "branch_coverage": 0.5,
                },
                seq=5,
            ),
            make_event(
                "context.projection",
                {"token_estimate": 1000},
                seq=6,
            ),
            make_event("run.succeeded", seq=7),
        ]
    )

    assert metrics["replan_count"] == 1
    assert metrics["false_completion"] == 0.0
    assert metrics["evidence_completeness"] == 2.0
    assert metrics["tool_selection_accuracy"] == 1.0
    assert metrics["context_waste"] == 1000.0
    assert metrics["branch_coverage"] == 0.5
    assert metrics["handoff_loss"] == 1
    assert metrics["dead_letter_rate"] == 0.0


def test_benchmark_compare_single_vs_graph() -> None:
    def single_runner(scenario: Scenario):
        return [
            make_event("run.started", seq=1),
            make_event("run.succeeded", seq=2),
        ]

    def graph_runner(scenario: Scenario):
        return [
            make_event("run.started", seq=1),
            make_event("finding.verified", {"finding_id": "finding_authz_admin"}, seq=2),
            make_event("run.succeeded", seq=3),
        ]

    single = BenchmarkRunner(single_runner).run(
        Scenario(
            scenario_id="s",
            name="s",
            target_ref="t",
            mode="single",
        ),
        runs=2,
    )
    graph = BenchmarkRunner(graph_runner).run(
        Scenario(
            scenario_id="s",
            name="s",
            target_ref="t",
            mode="graph",
        ),
        runs=2,
    )

    delta = BenchmarkRunner.compare(single, graph)
    assert delta["verified_avg"]["graph"] > delta["verified_avg"]["single"]
    assert delta["verified_avg"]["delta"] > 0


def test_behavior_snapshot_diff() -> None:
    base = snapshot_from_components(
        snapshot_id="s1",
        config={"provider": "remote"},
        harness={"tools": ["proxy.list"]},
        provider="remote",
    )
    changed = snapshot_from_components(
        snapshot_id="s1",
        config={"provider": "remote"},
        harness={"tools": ["proxy.list", "evidence.replay"]},
        provider="remote",
    )

    assert base.diff(changed) == ["harness_digest"]
    assert base.diff(base) == []


def test_snapshot_from_harness_maps_projection_changes() -> None:
    from services.agent_runtime.kernel.contracts import HarnessSnapshot

    def harness(tool_digest: str) -> HarnessSnapshot:
        return HarnessSnapshot(
            harness_id="h1",
            node_id="discovery",
            graph_version="v1",
            target_ref="https://lab.example.test",
            scope_hash="scope",
            auth_context_ref="auth",
            tool_projection_digest=tool_digest,
            skill_projection_digest="s",
            knowledge_view_digest="k",
            memory_view_digest="m",
            sandbox_profile="S2",
            network_profile="isolated_proxy",
            oracle_policy="verify_required",
            stop_policy="turn_budget",
            budget_policy="bounded",
            provider_capability="tool_calling",
            builder_version="1",
        )

    base = snapshot_from_harness(
        harness("tools-a"),
        snapshot_id="s1",
        provider="remote",
    )
    changed = snapshot_from_harness(
        harness("tools-b"),
        snapshot_id="s1",
        provider="remote",
    )

    assert base.harness_digest != changed.harness_digest
    assert base.diff(changed) == ["harness_digest"]
