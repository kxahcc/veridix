from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    AgentRunSpec,
    ExecutionRequest,
    ToolCall,
)
from services.agent_runtime.kernel.fake_runner import FakeRunner
from services.agent_runtime.kernel.tool_broker import ToolBroker


def _spec() -> AgentRunSpec:
    return AgentRunSpec(
        run_id="run_risk",
        mission_id="mission_1",
        target_ref="https://lab.example.test",
        behavior_snapshot="behavior_risk",
        allowed_targets=("https://lab.example.test",),
        allowed_tools=("shell.probe", "metasploit.console"),
        mission="risk policy test",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
        max_tool_risk="L3",
    )


def _risk(name: str) -> str:
    return "L4" if name == "metasploit.console" else "L1"


def test_broker_denies_tool_above_max_risk() -> None:
    runner = FakeRunner()
    broker = ToolBroker(
        runner,
        risk_resolver=_risk,
        max_risk_level="L3",
    )

    decision = broker.authorize(
        ToolCall(
            id="call_1",
            name="metasploit.console",
            arguments={"module": "auxiliary/scanner/http/title"},
        ),
        _spec(),
    )

    assert decision.allowed is False
    assert decision.rule == "risk_level_denied"
    assert decision.risk_level == "L4"


def test_broker_allows_tool_within_risk_limit() -> None:
    broker = ToolBroker(
        FakeRunner(),
        risk_resolver=_risk,
        max_risk_level="L3",
    )

    decision = broker.authorize(
        ToolCall(
            id="call_2",
            name="shell.probe",
            arguments={"target": "https://lab.example.test"},
        ),
        _spec(),
    )

    assert decision.allowed is True


def test_broker_allows_hostname_target_matching_allowed_url() -> None:
    broker = ToolBroker(FakeRunner())
    spec = AgentRunSpec(
        run_id="run_host",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_host",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("nmap.scan",),
        mission="recon",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )

    decision = broker.authorize(
        ToolCall(
            id="call_nmap",
            name="nmap.scan",
            arguments={"target": "compose-dvwa-1", "ports": "80"},
        ),
        spec,
    )

    assert decision.allowed is True


def test_broker_reuses_cached_result_for_same_arguments() -> None:
    runner = FakeRunner()
    broker = ToolBroker(runner)

    first = broker.execute(
        ExecutionRequest(
            action_id="a1",
            run_id="run_cache",
            tool_ref="shell.probe",
            input={"target": "https://lab.example.test"},
            idempotency_key="cache:1",
        )
    )
    second = broker.execute(
        ExecutionRequest(
            action_id="a2",
            run_id="run_cache",
            tool_ref="shell.probe",
            input={"target": "https://lab.example.test"},
            idempotency_key="cache:2",
        )
    )

    assert first.replayed is False
    assert second.replayed is True
    assert len(runner.executions) == 1


def test_broker_restores_cache_from_checkpoint() -> None:
    runner = FakeRunner()
    broker = ToolBroker(runner)
    broker.execute(
        ExecutionRequest(
            action_id="a1",
            run_id="run_restore",
            tool_ref="shell.probe",
            input={"target": "https://lab.example.test"},
            idempotency_key="restore:1",
        )
    )
    keys = broker.snapshot_keys()
    outcomes = broker.snapshot_outcomes()

    restored_runner = FakeRunner()
    restored = ToolBroker(restored_runner)
    restored.restore_outcomes(outcomes)
    restored.restore_keys(keys)
    result = restored.execute(
        ExecutionRequest(
            action_id="a2",
            run_id="run_restore",
            tool_ref="shell.probe",
            input={"target": "https://lab.example.test"},
            idempotency_key="restore:2",
        )
    )

    assert result.replayed is True
    assert len(restored_runner.executions) == 0


def test_broker_denies_unrelated_hostname_target() -> None:
    broker = ToolBroker(FakeRunner())
    spec = AgentRunSpec(
        run_id="run_host2",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_host2",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("nmap.scan",),
        mission="recon",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )

    decision = broker.authorize(
        ToolCall(
            id="call_nmap",
            name="nmap.scan",
            arguments={"target": "evil.example"},
        ),
        spec,
    )

    assert decision.allowed is False
    assert decision.rule == "target_out_of_scope"

def test_broker_without_resolver_keeps_legacy_allowlist_behavior() -> None:
    broker = ToolBroker(FakeRunner())

    decision = broker.authorize(
        ToolCall(
            id="call_3",
            name="metasploit.console",
            arguments={"module": "x"},
        ),
        _spec(),
    )

    assert decision.allowed is True
