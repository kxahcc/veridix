from __future__ import annotations

import pytest

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    LoopSpec,
    NodeSpec,
    OracleResult,
)
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.memory import InMemoryCheckpointStore
from services.agent_runtime.kernel.memory import SqliteCheckpointStore
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
from services.mission_orchestrator.scheduler import GraphScheduler


def discovery_spec() -> LoopSpec:
    return LoopSpec(
        loop_id="loop_discovery",
        profile="web_discovery",
        max_iterations=4,
        allowed_tools=("proxy.list",),
        budget={"known_endpoints": ("/", "/admin", "/api/health")},
    )


def verifier_spec() -> LoopSpec:
    return LoopSpec(
        loop_id="loop_verifier",
        profile="verifier",
        max_iterations=4,
        allowed_tools=("evidence.replay",),
        budget={"hypotheses": ("/admin",)},
    )


def make_factory():
    def factory(spec: LoopSpec) -> LoopRunner:
        if spec.profile == "web_discovery":
            model = ScriptedLoopModel(
                [
                    action(
                        ActionProposal(
                            action_id="d1",
                            tool_ref="proxy.list",
                            input={"path": "/"},
                        )
                    ),
                    finish("coverage done"),
                ]
            )
            return LoopRunner(
                spec,
                model,
                WebDiscoveryTool(("/", "/admin", "/api/health")),
                WebDiscoveryOracle(),
            )
        if spec.profile == "verifier":
            model = ScriptedLoopModel(
                [
                    action(
                        ActionProposal(
                            action_id="v1",
                            tool_ref="evidence.replay",
                            input={"candidate": "/admin"},
                        )
                    ),
                    finish("verify done"),
                ]
            )
            return LoopRunner(spec, model, VerifierTool({"/admin": "replay://proof"}), VerifierOracle())
        raise AssertionError(spec.profile)

    return factory


def make_scheduler() -> GraphScheduler:
    blackboard = Blackboard("graph_1")
    scheduler = GraphScheduler(
        graph_id="graph_1",
        mission_ref="mission_1",
        nodes={
            "discovery": NodeSpec(
                node_id="discovery",
                node_type="loop",
                loop_spec=discovery_spec(),
            ),
            "verifier": NodeSpec(
                node_id="verifier",
                node_type="loop",
                loop_spec=verifier_spec(),
                preconditions=("/admin",),
            ),
        },
        edges={"discovery": ("verifier",)},
        blackboard=blackboard,
        runner_factory=make_factory(),
        target_ref="https://lab.example.test",
    )
    return scheduler


def test_discovery_then_verifier_with_blackboard_handoff() -> None:
    scheduler = make_scheduler()

    discovery = scheduler.execute_node("discovery")
    verifier = scheduler.execute_node("verifier")

    assert discovery.status == "succeeded"
    assert verifier.status == "succeeded"
    assert len(scheduler.handoffs) == 1
    assert scheduler.handoffs[0].to_node == "verifier"
    assert any(
        view.fact.predicate == "handed_to"
        for view in scheduler._blackboard.projection()
    )
    metrics = scheduler.metrics()
    assert metrics.handoffs == 1
    assert metrics.dead_letters == 0


def test_replan_keeps_old_snapshot_immutable() -> None:
    scheduler = make_scheduler()
    patch = GraphPatch(
        patch_id="patch_1",
        parent_version="v1",
        author="planner",
        reason="add report",
        affected_nodes=("report",),
        operations=(
            {
                "op": "add_node",
                "node": NodeSpec(node_id="report", node_type="report"),
            },
        ),
        policy_checked=True,
    )

    snapshot = scheduler.apply_patch(patch)

    assert snapshot.version == "v2"
    assert "report" in scheduler.current_snapshot.state.node_states
    assert "report" not in scheduler.snapshot("v1").state.node_states

    bad = GraphPatch(
        patch_id="patch_bad",
        parent_version="v2",
        author="planner",
        reason="bypass",
        affected_nodes=("x",),
        operations=(),
        policy_checked=False,
    )
    with pytest.raises(ValueError, match="policy"):
        scheduler.apply_patch(bad)


def test_failing_node_reaches_dead_letter() -> None:
    blackboard = Blackboard("graph_2")

    def factory(spec: LoopSpec) -> LoopRunner:
        return LoopRunner(
            spec,
            ScriptedLoopModel([finish("nothing")]),
            VerifierTool({}),
            WebDiscoveryOracle(),
        )

    scheduler = GraphScheduler(
        graph_id="graph_2",
        mission_ref="mission_2",
        nodes={
            "discovery": NodeSpec(
                node_id="discovery",
                node_type="loop",
                loop_spec=LoopSpec(
                    loop_id="loop",
                    profile="stub",
                    max_iterations=1,
                ),
            ),
        },
        edges={},
        blackboard=blackboard,
        runner_factory=factory,
        target_ref="https://lab.example.test",
        max_retries=1,
    )

    first = scheduler.execute_node("discovery")
    assert first.status == "inconclusive"

    second = scheduler.execute_node("discovery")
    assert second.status == "dead_letter"
    assert second.dead_letter is True
    assert scheduler.metrics().dead_letters == 1


def test_run_ready_retries_failed_node_until_dead_letter() -> None:
    blackboard = Blackboard("graph_retry")

    def factory(spec: LoopSpec) -> LoopRunner:
        return LoopRunner(
            spec,
            ScriptedLoopModel([finish("nothing")]),
            VerifierTool({}),
            WebDiscoveryOracle(),
        )

    scheduler = GraphScheduler(
        graph_id="graph_retry",
        mission_ref="mission_retry",
        nodes={
            "discovery": NodeSpec(
                node_id="discovery",
                node_type="loop",
                loop_spec=LoopSpec(
                    loop_id="loop_retry",
                    profile="stub",
                    max_iterations=1,
                ),
            ),
        },
        edges={},
        blackboard=blackboard,
        runner_factory=factory,
        target_ref="https://lab.example.test",
        max_retries=1,
    )

    scheduler.run_ready()

    state = scheduler.state.node_states["discovery"]
    assert state.status == "dead_letter"
    assert state.retries == 2
    assert scheduler.metrics().dead_letters == 1


def test_run_ready_recovers_transient_failure() -> None:
    class FlakyOracle:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, state, facts, coverage) -> OracleResult:
            self.calls += 1
            if self.calls == 1:
                return OracleResult(
                    status="not_verified",
                    reason="first_failure",
                )
            return OracleResult(status="verified", reason="second_attempt")

    oracle = FlakyOracle()
    blackboard = Blackboard("graph_recover")

    def factory(spec: LoopSpec) -> LoopRunner:
        return LoopRunner(
            spec,
            ScriptedLoopModel([finish("done")]),
            VerifierTool({}),
            oracle,
        )

    scheduler = GraphScheduler(
        graph_id="graph_recover",
        mission_ref="mission_recover",
        nodes={
            "discovery": NodeSpec(
                node_id="discovery",
                node_type="loop",
                loop_spec=LoopSpec(
                    loop_id="loop_recover",
                    profile="stub",
                    max_iterations=1,
                ),
            ),
        },
        edges={},
        blackboard=blackboard,
        runner_factory=factory,
        target_ref="https://lab.example.test",
        max_retries=1,
    )

    scheduler.run_ready()

    state = scheduler.state.node_states["discovery"]
    assert state.status == "succeeded"
    assert state.retries == 1
    assert scheduler.metrics().dead_letters == 0
    assert scheduler.metrics().node_recovery_rate == 1.0


class GraphRetryOracle:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, state, facts, coverage) -> OracleResult:
        self.calls += 1
        if self.calls == 1:
            return OracleResult(
                status="not_verified",
                reason="first_attempt_incomplete",
            )
        return OracleResult(status="verified", reason="second_attempt")


def test_graph_scheduler_resumes_loop_checkpoint() -> None:
    checkpoint_store = InMemoryCheckpointStore()
    tool = WebDiscoveryTool(("/",))
    oracle = GraphRetryOracle()
    decisions = [
        action(
            ActionProposal(
                action_id="cp_graph_action",
                tool_ref="proxy.list",
                input={"path": "/"},
            )
        ),
        finish("resume done"),
    ]
    index = 0

    def factory(spec: LoopSpec) -> LoopRunner:
        nonlocal index
        item = decisions[index]
        index += 1
        return LoopRunner(
            spec,
            ScriptedLoopModel([item]),
            tool,
            oracle,
        )

    first_spec = LoopSpec(
        loop_id="loop_graph_cp",
        profile="web_discovery",
        max_iterations=1,
        allowed_tools=("proxy.list",),
        budget={"known_endpoints": ("/", "/admin")},
    )
    first = GraphScheduler(
        graph_id="graph_cp",
        mission_ref="mission_cp",
        nodes={
            "discovery": NodeSpec(
                node_id="discovery",
                node_type="loop",
                loop_spec=first_spec,
            ),
        },
        edges={},
        blackboard=Blackboard("graph_cp"),
        runner_factory=factory,
        target_ref="https://lab.example.test",
        loop_checkpoint_store=checkpoint_store,
    )

    first.execute_node("discovery")

    assert first.state.node_states["discovery"].status == "inconclusive"
    assert len(tool.executions) == 1

    resume_spec = LoopSpec(
        loop_id="loop_graph_cp",
        profile="web_discovery",
        max_iterations=2,
        allowed_tools=("proxy.list",),
        budget={"known_endpoints": ("/", "/admin")},
    )
    second = GraphScheduler(
        graph_id="graph_cp",
        mission_ref="mission_cp",
        nodes={
            "discovery": NodeSpec(
                node_id="discovery",
                node_type="loop",
                loop_spec=resume_spec,
            ),
        },
        edges={},
        blackboard=Blackboard("graph_cp"),
        runner_factory=factory,
        target_ref="https://lab.example.test",
        loop_checkpoint_store=checkpoint_store,
    )

    second.execute_node("discovery")

    assert second.state.node_states["discovery"].status == "succeeded"
    assert len(tool.executions) == 1


def test_graph_scheduler_resumes_loop_checkpoint_from_sqlite(
    tmp_path,
) -> None:
    checkpoint_path = tmp_path / "loop-checkpoints.sqlite3"
    first_store = SqliteCheckpointStore(checkpoint_path)
    tool = WebDiscoveryTool(("/",))
    oracle = GraphRetryOracle()
    decisions = [
        action(
            ActionProposal(
                action_id="cp_sqlite_action",
                tool_ref="proxy.list",
                input={"path": "/"},
            )
        ),
        finish("resume done"),
    ]
    index = 0

    def factory(spec: LoopSpec) -> LoopRunner:
        nonlocal index
        item = decisions[index]
        index += 1
        return LoopRunner(
            spec,
            ScriptedLoopModel([item]),
            tool,
            oracle,
        )

    first = GraphScheduler(
        graph_id="graph_cp_sqlite",
        mission_ref="mission_cp_sqlite",
        nodes={
            "discovery": NodeSpec(
                node_id="discovery",
                node_type="loop",
                loop_spec=LoopSpec(
                    loop_id="loop_graph_cp_sqlite",
                    profile="web_discovery",
                    max_iterations=1,
                    allowed_tools=("proxy.list",),
                    budget={"known_endpoints": ("/", "/admin")},
                ),
            ),
        },
        edges={},
        blackboard=Blackboard("graph_cp_sqlite"),
        runner_factory=factory,
        target_ref="https://lab.example.test",
        loop_checkpoint_store=first_store,
    )

    first.execute_node("discovery")

    assert len(tool.executions) == 1
    assert first_store.load("graph:graph_cp_sqlite:discovery") is not None
    first_store.close()

    second_store = SqliteCheckpointStore(checkpoint_path)
    second = GraphScheduler(
        graph_id="graph_cp_sqlite",
        mission_ref="mission_cp_sqlite",
        nodes={
            "discovery": NodeSpec(
                node_id="discovery",
                node_type="loop",
                loop_spec=LoopSpec(
                    loop_id="loop_graph_cp_sqlite",
                    profile="web_discovery",
                    max_iterations=2,
                    allowed_tools=("proxy.list",),
                    budget={"known_endpoints": ("/", "/admin")},
                ),
            ),
        },
        edges={},
        blackboard=Blackboard("graph_cp_sqlite"),
        runner_factory=factory,
        target_ref="https://lab.example.test",
        loop_checkpoint_store=second_store,
    )

    second.execute_node("discovery")

    assert second.state.node_states["discovery"].status == "succeeded"
    assert len(tool.executions) == 1
    second_store.close()
