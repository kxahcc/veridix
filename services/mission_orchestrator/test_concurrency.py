from __future__ import annotations

import pytest

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    LoopSpec,
    NodeSpec,
)
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loops import (
    ScriptedLoopModel,
    WebDiscoveryOracle,
    WebDiscoveryTool,
    action,
    finish,
)
from services.mission_orchestrator.blackboard import Blackboard
from services.mission_orchestrator.contracts import BackpressureError
from services.mission_orchestrator.lease import LeaseRegistry
from services.mission_orchestrator.scheduler import GraphScheduler


def discovery_node(node_id: str) -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id=node_id,
            profile="web_discovery",
            max_iterations=4,
            allowed_tools=("proxy.list",),
            budget={"known_endpoints": ("/",)},
        ),
    )


def factory(spec: LoopSpec) -> LoopRunner:
    return LoopRunner(
        spec,
        ScriptedLoopModel(
            [
                action(
                    ActionProposal(
                        action_id=f"{spec.loop_id}_d1",
                        tool_ref="proxy.list",
                        input={"path": "/"},
                    )
                ),
                finish("coverage complete"),
            ]
        ),
        WebDiscoveryTool(("/",)),
        WebDiscoveryOracle(),
    )


def test_parallel_group_executes_with_leases() -> None:
    registry = LeaseRegistry(":memory:")
    scheduler = GraphScheduler(
        graph_id="graph_par",
        mission_ref="mission_par",
        nodes={
            "n1": discovery_node("n1"),
            "n2": discovery_node("n2"),
        },
        edges={},
        blackboard=Blackboard("graph_par"),
        runner_factory=factory,
        target_ref="https://lab.example.test",
        leases=registry,
    )

    results = scheduler.execute_group(
        ["n1", "n2"],
        worker_id="worker_1",
        parallel=True,
    )

    assert results["n1"].status == "succeeded"
    assert results["n2"].status == "succeeded"
    assert registry.acquire("n1", "worker_2") is not None
    assert registry.acquire("n2", "worker_2") is not None


def test_backpressure_raises_when_handoff_limit_reached() -> None:
    scheduler = GraphScheduler(
        graph_id="graph_bp",
        mission_ref="mission_bp",
        nodes={"discovery": discovery_node("discovery")},
        edges={"discovery": ("verifier",)},
        blackboard=Blackboard("graph_bp"),
        runner_factory=factory,
        target_ref="https://lab.example.test",
        max_pending_handoffs=0,
    )

    with pytest.raises(BackpressureError, match="pending handoff limit"):
        scheduler.execute_node("discovery")


def test_backpressure_queue_drains_after_capacity_returns() -> None:
    scheduler = GraphScheduler(
        graph_id="graph_bp_queue",
        mission_ref="mission_bp_queue",
        nodes={
            "discovery_a": discovery_node("discovery_a"),
            "discovery_b": discovery_node("discovery_b"),
        },
        edges={
            "discovery_a": ("verifier",),
            "discovery_b": ("verifier",),
        },
        blackboard=Blackboard("graph_bp_queue"),
        runner_factory=factory,
        target_ref="https://lab.example.test",
        max_pending_handoffs=1,
    )

    scheduler.execute_node_queued("discovery_a")
    waiting = scheduler.execute_node_queued("discovery_b")

    assert waiting.status == "backpressure_waiting"
    assert scheduler.drain_pending_fanouts() == 0

    scheduler._handoffs.clear()
    assert scheduler.drain_pending_fanouts() == 1
    assert scheduler.state.node_states["discovery_b"].status == "succeeded"
