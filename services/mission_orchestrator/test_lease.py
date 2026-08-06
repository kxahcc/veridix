from __future__ import annotations

from services.agent_runtime.kernel.contracts import LoopSpec, NodeSpec
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loops import (
    ScriptedLoopModel,
    WebDiscoveryOracle,
    WebDiscoveryTool,
    finish,
)
from services.mission_orchestrator.blackboard import Blackboard
from services.mission_orchestrator.lease import LeaseRegistry
from services.mission_orchestrator.scheduler import GraphScheduler


def test_lease_registry_conflict_release_and_expiry() -> None:
    registry = LeaseRegistry(":memory:")

    first = registry.acquire("node_1", "worker_1", lease_seconds=60)
    assert first is not None
    assert registry.acquire("node_1", "worker_2", lease_seconds=60) is None

    registry.release("node_1")
    assert registry.acquire("node_1", "worker_2", lease_seconds=60) is not None

    assert registry.expire(now="2999-01-01T00:00:00Z") == 1


def test_execute_group_respects_leases() -> None:
    blackboard = Blackboard("graph_lease")
    registry = LeaseRegistry(":memory:")
    scheduler = GraphScheduler(
        graph_id="graph_lease",
        mission_ref="mission_lease",
        nodes={
            "discovery": NodeSpec(
                node_id="discovery",
                node_type="loop",
                loop_spec=LoopSpec(
                    loop_id="loop",
                    profile="web_discovery",
                    max_iterations=1,
                ),
            ),
        },
        edges={},
        blackboard=blackboard,
        runner_factory=lambda spec: LoopRunner(
            spec,
            ScriptedLoopModel([finish("done")]),
            WebDiscoveryTool(("/",)),
            WebDiscoveryOracle(),
        ),
        target_ref="https://lab.example.test",
        leases=registry,
    )

    first = scheduler.execute_group(["discovery"], worker_id="worker_1")
    assert first["discovery"].status in ("succeeded", "inconclusive")

    registry.acquire("discovery", "blocker", lease_seconds=60)
    blocked = scheduler.execute_group(["discovery"], worker_id="worker_2")
    assert blocked["discovery"].status == "lease_conflict"


def test_human_gate_marks_waiting() -> None:
    scheduler = GraphScheduler(
        graph_id="graph_human",
        mission_ref="mission_human",
        nodes={
            "review": NodeSpec(node_id="review", node_type="human"),
        },
        edges={},
        blackboard=Blackboard("graph_human"),
        runner_factory=lambda spec: None,
        target_ref="https://lab.example.test",
    )

    state = scheduler.execute_node("review")

    assert state.status == "waiting_human"
