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

from .web_appsec import build_web_appsec_graph, build_web_appsec_scheduler


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
            WebDiscoveryTool(("/", "/admin", "/api/health")),
            WebDiscoveryOracle(),
        )
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


def test_web_appsec_graph_template_shape() -> None:
    nodes, edges = build_web_appsec_graph()

    assert set(nodes) == {"discovery", "verifier"}
    assert nodes["discovery"].loop_spec is not None
    assert nodes["discovery"].loop_spec.profile == "web_discovery"
    assert nodes["verifier"].preconditions == ("/admin",)
    assert edges == {"discovery": ("verifier",)}


def test_web_appsec_scheduler_runs_discovery_then_verifier() -> None:
    scheduler = build_web_appsec_scheduler(
        graph_id="web-appsec-1",
        mission_ref="m1",
        target_ref="https://lab.example.test",
        runner_factory=factory,
    )

    scheduler.execute_node("discovery")
    scheduler.execute_node("verifier")

    assert scheduler.state.node_states["discovery"].status == "succeeded"
    assert scheduler.state.node_states["verifier"].status == "succeeded"
    assert len(scheduler.handoffs) == 1
