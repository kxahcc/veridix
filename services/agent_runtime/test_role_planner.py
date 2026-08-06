from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    LoopSpec,
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
from services.agent_runtime.roles import AgentRole, RoleGraphRunner
from services.mission_orchestrator.planner import CandidateVerifierPlanner


TARGET = "https://lab.example.test"


def _role_factory():
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
                VerifierTool({"/admin": "replay://proof/admin"}),
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


def test_planner_adds_verifier_when_candidates_emerge() -> None:
    roles = (
        AgentRole(
            role_id="discovery",
            profile="web_discovery",
            allowed_tools=("proxy.list",),
            budget={
                "known_endpoints": ("/", "/admin"),
                "hypotheses": ("/admin",),
            },
        ),
    )
    runner = RoleGraphRunner(
        roles=roles,
        runner_factory=_role_factory(),
        graph_id="graph_planner",
        mission_ref="mission_planner",
        target_ref=TARGET,
        planner=CandidateVerifierPlanner(),
    )

    result = runner.run()

    statuses = dict(result.node_statuses)
    assert "verifier" in statuses
    assert statuses["discovery"] == "succeeded"
    assert statuses["verifier"] == "succeeded"
    assert result.metrics.replans == 1
