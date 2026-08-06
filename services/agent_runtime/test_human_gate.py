from __future__ import annotations

from services.agent_runtime.roles import AgentRole, RoleGraphRunner


TARGET = "https://lab.example.test"


def _roles() -> tuple[AgentRole, ...]:
    return (
        AgentRole(
            role_id="gate",
            node_type="human",
            human_prompt="approve active exploitation?",
        ),
        AgentRole(
            role_id="reporter",
            node_type="aggregate",
        ),
    )


def _runner(resolver) -> RoleGraphRunner:
    return RoleGraphRunner(
        roles=_roles(),
        runner_factory=lambda spec: None,  # type: ignore[arg-type]
        graph_id="graph_human",
        mission_ref="mission_human",
        target_ref=TARGET,
        human_resolver=resolver,
    )


def test_human_gate_without_decision_returns_waiting() -> None:
    result = _runner(lambda node_id, prompt: None).run()

    assert result.waiting is True
    assert result.waiting_nodes == ("gate",)
    assert dict(result.node_statuses)["gate"] == "waiting_human"


def test_human_gate_with_decision_continues_graph() -> None:
    result = _runner(lambda node_id, prompt: True).run()

    assert result.waiting is False
    assert dict(result.node_statuses)["gate"] == "succeeded"
    assert dict(result.node_statuses)["reporter"] == "succeeded"
    assert result.handoffs


def test_human_gate_rejection_blocks_downstream() -> None:
    result = _runner(lambda node_id, prompt: False).run()

    assert dict(result.node_statuses)["gate"] == "failed"
    assert dict(result.node_statuses)["reporter"] == "pending"
