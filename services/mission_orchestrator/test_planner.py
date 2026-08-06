from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
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
from services.mission_orchestrator.planner import (
    CoverageReplanner,
    ChainPlanner,
    FailureDrivenReplanner,
)
from services.mission_orchestrator.scheduler import GraphScheduler


def test_coverage_replanner_adds_verifier_after_discovery() -> None:
    blackboard = Blackboard("graph_plan")

    def factory(spec: LoopSpec) -> LoopRunner:
        if spec.profile == "web_discovery":
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
        raise AssertionError(spec.profile)

    discovery = NodeSpec(
        node_id="discovery",
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id="loop_discovery",
            profile="web_discovery",
            max_iterations=4,
            allowed_tools=("proxy.list",),
            budget={"known_endpoints": ("/", "/admin")},
        ),
    )
    verifier = NodeSpec(
        node_id="verifier",
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id="loop_verifier",
            profile="verifier",
            max_iterations=4,
            allowed_tools=("evidence.replay",),
            budget={"hypotheses": ("/admin",)},
        ),
        preconditions=("/admin",),
    )
    scheduler = GraphScheduler(
        graph_id="graph_plan",
        mission_ref="mission_plan",
        nodes={"discovery": discovery},
        edges={},
        blackboard=blackboard,
        runner_factory=factory,
        target_ref="https://lab.example.test",
    )
    scheduler.execute_node("discovery")
    replanner = CoverageReplanner(verifier_node=verifier)

    patch = replanner.propose(scheduler.state, blackboard)
    assert patch is not None
    assert patch.policy_checked is True

    scheduler.apply_patch(patch)
    state = scheduler.execute_node("verifier")
    assert state.status == "succeeded"

    second = replanner.propose(scheduler.state, blackboard)
    assert second is None


def test_failure_driven_replanner_adds_fallback_after_replan_signal() -> None:
    blackboard = Blackboard("graph_failure_plan")

    def factory(spec: LoopSpec) -> LoopRunner:
        if spec.profile == "web_discovery":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(
                            ActionProposal(
                                action_id="f1",
                                tool_ref="proxy.list",
                                input={"path": "/"},
                            )
                        ),
                        finish("fallback coverage complete"),
                    ]
                ),
                WebDiscoveryTool(("/",)),
                WebDiscoveryOracle(),
            )
        return LoopRunner(
            spec,
            ScriptedLoopModel([finish("nothing")]),
            WebDiscoveryTool(("/",)),
            WebDiscoveryOracle(),
        )

    scanner = NodeSpec(
        node_id="scanner",
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id="loop_scanner",
            profile="fail",
            max_iterations=1,
        ),
    )
    fallback = NodeSpec(
        node_id="fallback_scanner",
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id="loop_fallback",
            profile="web_discovery",
            max_iterations=2,
            allowed_tools=("proxy.list",),
            budget={"known_endpoints": ("/",)},
        ),
    )
    scheduler = GraphScheduler(
        graph_id="graph_failure_plan",
        mission_ref="mission_failure_plan",
        nodes={
            "scanner": scanner,
            "reporter": NodeSpec(node_id="reporter", node_type="aggregate"),
        },
        edges={"scanner": ("reporter",)},
        blackboard=blackboard,
        runner_factory=factory,
        target_ref="https://lab.example.test",
    )
    scheduler.execute_node("scanner")
    replanner = FailureDrivenReplanner(
        fallback_node=fallback,
        failed_node="scanner",
        target_node="reporter",
    )

    patch = replanner.propose(
        scheduler.current_snapshot,
        blackboard,
        diagnostics={
            "scanner": [
                {
                    "event_type": "loop.replan.suggested",
                    "payload": {
                        "reason": "tool_repeated_failure",
                        "tool": "nmap.scan",
                    },
                }
            ]
        },
    )

    assert patch is not None
    scheduler.apply_patch(patch)
    scheduler.run_ready()

    assert (
        scheduler.state.node_states["fallback_scanner"].status == "succeeded"
    )
    assert scheduler.state.node_states["reporter"].status == "succeeded"
    assert scheduler.metrics().replans == 1


def test_failure_driven_replanner_ignores_healthy_graph() -> None:
    blackboard = Blackboard("graph_healthy_plan")
    scheduler = GraphScheduler(
        graph_id="graph_healthy_plan",
        mission_ref="mission_healthy_plan",
        nodes={
            "scanner": NodeSpec(
                node_id="scanner",
                node_type="aggregate",
            ),
        },
        edges={},
        blackboard=blackboard,
        runner_factory=lambda spec: None,
        target_ref="https://lab.example.test",
    )
    scheduler.execute_node("scanner")
    replanner = FailureDrivenReplanner(
        fallback_node=NodeSpec(
            node_id="fallback_scanner",
            node_type="aggregate",
        ),
        failed_node="scanner",
    )

    patch = replanner.propose(
        scheduler.current_snapshot,
        blackboard,
        diagnostics={},
    )

    assert patch is None


def test_chain_planner_returns_first_viable_patch() -> None:
    blackboard = Blackboard("graph_chain_plan")
    scheduler = GraphScheduler(
        graph_id="graph_chain_plan",
        mission_ref="mission_chain_plan",
        nodes={
            "scanner": NodeSpec(
                node_id="scanner",
                node_type="aggregate",
            ),
            "reporter": NodeSpec(
                node_id="reporter",
                node_type="aggregate",
            ),
        },
        edges={"scanner": ("reporter",)},
        blackboard=blackboard,
        runner_factory=lambda spec: None,
        target_ref="https://lab.example.test",
    )
    scheduler.execute_node("scanner")
    fallback = NodeSpec(
        node_id="fallback_scanner",
        node_type="aggregate",
    )
    chain = ChainPlanner(
        (
            FailureDrivenReplanner(
                fallback_node=fallback,
                failed_node="missing",
            ),
            FailureDrivenReplanner(
                fallback_node=fallback,
                failed_node="scanner",
                target_node="reporter",
            ),
        )
    )

    patch = chain.propose(
        scheduler.current_snapshot,
        blackboard,
        diagnostics={
            "scanner": [{"event_type": "loop.replan.suggested"}]
        },
    )

    assert patch is not None
    assert patch.reason == "failure_recovery_add_fallback"
