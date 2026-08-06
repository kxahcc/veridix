from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    LoopSpec,
)
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loops import (
    ScriptedLoopModel,
    action,
    finish,
)
from services.agent_runtime.kernel.security_loops import (
    AuthzMatrixOracle,
    AuthzMatrixTool,
    SSRFCallbackOracle,
    SSRFCallbackTool,
)
from services.agent_runtime.roles import (
    RoleGraphRunner,
    authz_matrix_role_template,
    ssrf_callback_role_template,
)


TARGET = "https://lab.example.test"


def _authz_action(role: str, endpoint: str) -> ActionProposal:
    return ActionProposal(
        action_id=f"authz_{role}",
        tool_ref="web.replay",
        input={
            "endpoint": endpoint,
            "role": role,
            "method": "GET",
            "object_id": "user_2",
        },
    )


def test_authz_matrix_loop_verifies_idor_evidence() -> None:
    spec = LoopSpec(
        loop_id="loop_authz",
        profile="authz_matrix",
        max_iterations=2,
        allowed_tools=("web.replay",),
    )
    tool = AuthzMatrixTool(
        matrix={
            ("/api/users/2", "user_a"): "denied",
        },
        outcomes={
            ("/api/users/2", "user_a"): "allowed",
        },
    )
    runner = LoopRunner(
        spec,
        ScriptedLoopModel(
            [
                action(_authz_action("user_a", "/api/users/2")),
                finish("matrix complete"),
            ]
        ),
        tool,
        AuthzMatrixOracle(),
    )

    result = runner.run()

    assert result.status == "succeeded"
    assert result.stop_reason == "oracle_verified"
    assert any(fact.predicate == "finding" for fact in result.facts)
    assert result.oracle_result is not None
    assert result.oracle_result.reason == "authz_matrix_evidence"


def test_authz_matrix_negative_evidence_is_not_verified() -> None:
    spec = LoopSpec(
        loop_id="loop_authz_neg",
        profile="authz_matrix",
        max_iterations=2,
        allowed_tools=("web.replay",),
    )
    tool = AuthzMatrixTool(
        matrix={
            ("/api/users/2", "user_a"): "allowed",
        },
        outcomes={
            ("/api/users/2", "user_a"): "denied",
        },
    )
    runner = LoopRunner(
        spec,
        ScriptedLoopModel(
            [
                action(_authz_action("user_a", "/api/users/2")),
                finish("matrix complete"),
            ]
        ),
        tool,
        AuthzMatrixOracle(),
    )

    result = runner.run()

    assert result.status == "inconclusive"
    assert any(
        fact.predicate == "negative_finding"
        for fact in result.facts
    )


def test_ssrf_callback_loop_verifies_one_time_evidence() -> None:
    spec = LoopSpec(
        loop_id="loop_ssrf",
        profile="ssrf_callback",
        max_iterations=2,
        allowed_tools=("oast.check",),
    )
    tool = SSRFCallbackTool(
        callbacks={
            "tok_123": {"source": "10.0.0.5"},
        }
    )
    runner = LoopRunner(
        spec,
        ScriptedLoopModel(
            [
                action(
                    ActionProposal(
                        action_id="ssrf_1",
                        tool_ref="oast.check",
                        input={
                            "url": f"{TARGET}/fetch",
                            "callback_token": "tok_123",
                        },
                    )
                ),
                finish("callback checked"),
            ]
        ),
        tool,
        SSRFCallbackOracle(),
    )

    result = runner.run()

    assert result.status == "succeeded"
    assert result.stop_reason == "oracle_verified"
    assert any(
        fact.predicate == "callback_evidence"
        for fact in result.facts
    )


def test_ssrf_pending_callback_stays_inconclusive() -> None:
    spec = LoopSpec(
        loop_id="loop_ssrf_pending",
        profile="ssrf_callback",
        max_iterations=2,
        allowed_tools=("oast.check",),
    )
    runner = LoopRunner(
        spec,
        ScriptedLoopModel(
            [
                action(
                    ActionProposal(
                        action_id="ssrf_2",
                        tool_ref="oast.check",
                        input={
                            "url": f"{TARGET}/fetch",
                            "callback_token": "tok_missing",
                        },
                    )
                ),
                finish("callback checked"),
            ]
        ),
        SSRFCallbackTool(),
        SSRFCallbackOracle(),
    )

    result = runner.run()

    assert result.status == "inconclusive"
    assert result.stop_reason == "oracle_not_verified"


def _domain_role_factory():
    def factory(spec: LoopSpec):
        if spec.profile == "authz_matrix":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(_authz_action("user_a", "/api/users/2")),
                        finish("matrix complete"),
                    ]
                ),
                AuthzMatrixTool(
                    matrix={
                        ("/api/users/2", "user_a"): "denied",
                    },
                    outcomes={
                        ("/api/users/2", "user_a"): "allowed",
                    },
                ),
                AuthzMatrixOracle(),
            )
        if spec.profile == "ssrf_callback":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(
                            ActionProposal(
                                action_id="ssrf_1",
                                tool_ref="oast.check",
                                input={
                                    "url": f"{TARGET}/fetch",
                                    "callback_token": "tok_123",
                                },
                            )
                        ),
                        finish("callback checked"),
                    ]
                ),
                SSRFCallbackTool(callbacks={"tok_123": {"source": "10.0.0.5"}}),
                SSRFCallbackOracle(),
            )
        raise AssertionError(spec.profile)

    return factory


def test_authz_matrix_role_template_runs_through_role_graph() -> None:
    runner = RoleGraphRunner(
        roles=authz_matrix_role_template(target_ref=TARGET),
        runner_factory=_domain_role_factory(),
        graph_id="graph_authz",
        mission_ref="mission_authz",
        target_ref=TARGET,
    )

    result = runner.run()

    assert dict(result.node_statuses)["authz_matrix"] == "succeeded"
    assert any(fact.predicate == "finding" for fact in result.facts)


def test_ssrf_role_template_runs_through_role_graph() -> None:
    runner = RoleGraphRunner(
        roles=ssrf_callback_role_template(target_ref=TARGET),
        runner_factory=_domain_role_factory(),
        graph_id="graph_ssrf",
        mission_ref="mission_ssrf",
        target_ref=TARGET,
    )

    result = runner.run()

    assert dict(result.node_statuses)["ssrf_callback"] == "succeeded"
    assert any(
        fact.predicate == "callback_evidence"
        for fact in result.facts
    )
