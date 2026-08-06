from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    FactRecord,
    LoopResult,
    LoopSpec,
    NodeSpec,
)
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loops import (
    ScriptedLoopModel,
    VerifierOracle,
    VerifierTool,
    WebDiscoveryOracle,
    WebDiscoveryTool,
    action,
    finish,
)
from services.mission_orchestrator.blackboard import Blackboard
from services.mission_orchestrator.contracts import GraphPatch
from services.mission_orchestrator.graph_store import GraphStore
from services.mission_orchestrator.scheduler import GraphScheduler


TARGET = "https://lab.example.test"


def _discovery_node(node_id: str = "discovery") -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id=f"loop_{node_id}",
            profile="web_discovery",
            max_iterations=2,
            allowed_tools=("proxy.list",),
            budget={"known_endpoints": ("/", "/admin")},
        ),
    )


def _verifier_node(node_id: str = "verifier") -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id=f"loop_{node_id}",
            profile="verifier",
            max_iterations=2,
            allowed_tools=("evidence.replay",),
            budget={"hypotheses": ("/admin",)},
        ),
        preconditions=("/admin",),
    )


def _factory():
    def factory(spec: LoopSpec) -> LoopRunner:
        if spec.profile == "verifier":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(
                            ActionProposal(
                                action_id="v1",
                                tool_ref="evidence.replay",
                                input={"candidate": "/admin"},
                            )
                        ),
                        finish("verified"),
                    ]
                ),
                VerifierTool({"/admin": "replay://proof"}),
                VerifierOracle(),
            )
        return LoopRunner(
            spec,
            ScriptedLoopModel(
                [
                    action(
                        ActionProposal(
                            action_id="d1",
                            tool_ref="proxy.list",
                            input={"path": "/"},
                        )
                    ),
                    finish("coverage complete"),
                ]
            ),
            WebDiscoveryTool(("/", "/admin")),
            WebDiscoveryOracle(),
        )

    return factory


def _scheduler(
    nodes: dict[str, NodeSpec],
    edges: dict[str, tuple[str, ...]],
    *,
    store: GraphStore | None = None,
) -> GraphScheduler:
    return GraphScheduler(
        graph_id="graph_test",
        mission_ref="mission_test",
        nodes=nodes,
        edges=edges,
        blackboard=Blackboard("graph_test"),
        runner_factory=_factory(),
        target_ref=TARGET,
        store=store,
    )


def test_graph_store_roundtrip_persists_structure_and_state(tmp_path) -> None:
    db = tmp_path / "graphs.db"
    nodes = {
        "discovery": _discovery_node(),
        "verifier": _verifier_node(),
    }
    edges = {"discovery": ("verifier",)}
    first = _scheduler(nodes, edges, store=GraphStore(db))
    first.execute_node("discovery")
    first.execute_node("verifier")

    restored = _scheduler(nodes, edges, store=GraphStore(db))

    assert restored.current_snapshot.version == "v1"
    assert restored.state.node_states["discovery"].status == "succeeded"
    assert restored.state.node_states["verifier"].status == "succeeded"
    assert restored.handoffs
    assert restored.metrics().replans == 0


def test_restore_reclaims_node_that_was_running_when_process_died(
    tmp_path,
) -> None:
    db = tmp_path / "graphs.db"
    nodes = {
        "discovery": _discovery_node(),
        "verifier": _verifier_node(),
    }
    edges = {"discovery": ("verifier",)}
    store = GraphStore(db)
    first = _scheduler(nodes, edges, store=store)
    first.state.node_states["discovery"].status = "running"
    first._persist()

    restored = _scheduler(nodes, edges, store=GraphStore(db))

    assert restored.state.node_states["discovery"].status == "pending"
    restored.run_ready()
    assert restored.state.node_states["discovery"].status == "succeeded"


def test_restore_reclaims_waiting_and_backpressure_nodes(tmp_path) -> None:
    db = tmp_path / "graphs.db"
    nodes = {
        "discovery": _discovery_node(),
        "verifier": _verifier_node(),
    }
    edges = {"discovery": ("verifier",)}
    store = GraphStore(db)
    first = _scheduler(nodes, edges, store=store)
    first.state.node_states["discovery"].status = "waiting"
    first.state.node_states["verifier"].status = "backpressure_waiting"
    first._persist()

    restored = _scheduler(nodes, edges, store=GraphStore(db))

    assert restored.state.node_states["discovery"].status == "pending"
    assert restored.state.node_states["verifier"].status == "pending"


def test_restore_keeps_dead_letter_and_replan_metrics(tmp_path) -> None:
    db = tmp_path / "graphs.db"
    nodes = {"discovery": _discovery_node()}
    store = GraphStore(db)
    first = _scheduler(nodes, {}, store=store)
    first.state.node_states["discovery"].status = "dead_letter"
    first.state.node_states["discovery"].dead_letter = True
    first._persist()
    store.save_patch(
        "graph_test",
        GraphPatch(
            patch_id="patch_metric",
            parent_version="v1",
            author="test",
            reason="metric_persist",
            affected_nodes=("fallback",),
            operations=(),
            policy_checked=True,
        ),
        "v2",
    )

    restored = _scheduler(nodes, {}, store=GraphStore(db))

    assert restored.metrics().dead_letters == 1
    assert restored.metrics().replans == 1


def test_graph_store_roundtrip_persists_fact_metadata(tmp_path) -> None:
    db = tmp_path / "graphs.db"
    nodes = {"discovery": _discovery_node()}
    store = GraphStore(db)
    scheduler = _scheduler(nodes, {}, store=store)
    scheduler.execute_node("discovery")
    state = scheduler.state.node_states["discovery"]
    state.result = LoopResult(
        status="succeeded",
        facts=(
            FactRecord(
                fact_id="fact_finding_1",
                subject=TARGET,
                predicate="finding",
                value="Exposure",
                metadata={
                    "vuln_category": "Exposure",
                    "severity": "medium",
                },
            ),
        ),
        evidence_refs=("artifact://scan/1",),
        candidate_findings=(),
        stop_reason="coverage_met",
    )
    scheduler._persist()

    restored = _scheduler(nodes, {}, store=GraphStore(db))
    restored_state = restored.state.node_states["discovery"]

    assert restored_state.result is not None
    assert restored_state.result.facts[0].metadata["vuln_category"] == "Exposure"
    assert restored_state.result.facts[0].metadata["severity"] == "medium"


def test_conditional_edges_skip_fanout_on_mismatch() -> None:
    nodes = {
        "discovery": _discovery_node(),
        "verifier": _verifier_node(),
    }
    nodes["discovery"] = NodeSpec(
        node_id="discovery",
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id="loop_discovery",
            profile="web_discovery",
            max_iterations=2,
            allowed_tools=("proxy.list",),
            budget={"known_endpoints": ("/", "/admin")},
        ),
        edge_conditions=("inconclusive",),
    )
    scheduler = _scheduler(nodes, {"discovery": ("verifier",)})

    scheduler.run_ready()

    assert scheduler.state.node_states["verifier"].status == "pending"
    assert scheduler.metrics().handoffs == 0


def test_run_ready_respects_fan_in_before_aggregate() -> None:
    nodes = {
        "source_a": _discovery_node("source_a"),
        "source_b": _discovery_node("source_b"),
        "aggregate": NodeSpec(
            node_id="aggregate",
            node_type="aggregate",
        ),
    }
    edges = {
        "source_a": ("aggregate",),
        "source_b": ("aggregate",),
    }
    scheduler = _scheduler(nodes, edges)

    scheduler.run_ready()

    assert scheduler.state.node_states["aggregate"].status == "succeeded"
    assert scheduler.metrics().handoffs == 2
    assert scheduler.metrics().branch_coverage == 1.0


def test_apply_patch_is_persisted_and_counted() -> None:
    store = GraphStore(":memory:")
    scheduler = _scheduler(
        {"discovery": _discovery_node()},
        {},
        store=store,
    )
    scheduler.execute_node("discovery")
    patch = GraphPatch(
        patch_id="patch_test",
        parent_version=scheduler.current_snapshot.version,
        author="test",
        reason="add_verifier",
        affected_nodes=("verifier",),
        operations=(
            {"op": "add_node", "node": _verifier_node()},
            {
                "op": "add_edge",
                "source": "discovery",
                "target": "verifier",
            },
        ),
        policy_checked=True,
    )
    scheduler.apply_patch(patch)

    assert scheduler.metrics().replans == 1
    assert store.load("graph_test") is not None


def test_human_gate_waits_and_resolves() -> None:
    nodes = {
        "discovery": _discovery_node(),
        "gate": NodeSpec(
            node_id="gate",
            node_type="human",
            human_prompt="confirm scope for active exploitation",
        ),
        "reporter": NodeSpec(
            node_id="reporter",
            node_type="aggregate",
        ),
    }
    edges = {
        "discovery": ("gate",),
        "gate": ("reporter",),
    }
    scheduler = _scheduler(nodes, edges)

    scheduler.execute_node("discovery")
    scheduler.run_ready()

    gate_state = scheduler.state.node_states["gate"]
    assert gate_state.status == "waiting_human"
    assert gate_state.human_payload is not None
    assert "confirm scope" in gate_state.human_payload["prompt"]
    assert scheduler.state.node_states["reporter"].status == "pending"

    scheduler.resolve_human("gate", approved=True, reason="operator ok")

    scheduler.run_ready()
    assert scheduler.state.node_states["gate"].status == "succeeded"
    assert scheduler.state.node_states["reporter"].status == "succeeded"


def test_human_gate_rejection_blocks_downstream() -> None:
    nodes = {
        "gate": NodeSpec(
            node_id="gate",
            node_type="human",
            human_prompt="approve?",
        ),
        "reporter": NodeSpec(
            node_id="reporter",
            node_type="aggregate",
        ),
    }
    scheduler = _scheduler(nodes, {"gate": ("reporter",)})
    scheduler.run_ready()

    scheduler.resolve_human("gate", approved=False, reason="denied by operator")

    scheduler.run_ready()
    assert scheduler.state.node_states["gate"].status == "failed"
    assert scheduler.state.node_states["reporter"].status == "pending"


def test_human_gate_state_survives_persistence(tmp_path) -> None:
    db = tmp_path / "graphs.db"
    nodes = {
        "gate": NodeSpec(
            node_id="gate",
            node_type="human",
            human_prompt="approve?",
        ),
    }
    store = GraphStore(db)
    first = _scheduler(nodes, {}, store=store)
    first.run_ready()

    restored = _scheduler(nodes, {}, store=GraphStore(db))

    assert restored.state.node_states["gate"].status == "waiting_human"
    assert restored.state.node_states["gate"].human_payload is not None
