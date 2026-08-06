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
from services.agent_runtime.role_benchmark import (
    compare_single_vs_multi_role,
)


def _working_factory():
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

    return factory


def test_compare_single_vs_multi_role_returns_recommendation() -> None:
    report = compare_single_vs_multi_role(
        target_ref="https://lab.example.test",
        runner_factory=_working_factory(),
    )

    assert report["single"]["verified"] is True
    assert report["graph"]["verified"] is True
    assert report["graph"]["handoffs"] >= 1
    assert report["recommendation"] in ("single", "graph")
    assert set(report) == {
        "target_ref",
        "runs",
        "single_verified_runs",
        "graph_verified_runs",
        "single",
        "graph",
        "recommendation",
    }
