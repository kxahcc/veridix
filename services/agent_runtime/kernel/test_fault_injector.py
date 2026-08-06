from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    AgentRunSpec,
    ExecutionRequest,
    LoopToolResult,
)
from services.agent_runtime.kernel.fake_runner import FakeRunner
from services.agent_runtime.kernel.fault_injector import FaultInjector
from services.agent_runtime.kernel.loop_adapters import BrokerLoopTool
from services.agent_runtime.kernel.tool_broker import ToolBroker


TARGET = "http://lab.example.test"


def _spec() -> AgentRunSpec:
    return AgentRunSpec(
        run_id="run_fault",
        mission_id="mission_1",
        target_ref=TARGET,
        behavior_snapshot="b1",
        allowed_targets=(TARGET,),
        allowed_tools=("shell.probe", "run.finish"),
        max_turns=3,
    )


def _proposal() -> ActionProposal:
    return ActionProposal(
        action_id="a1",
        tool_ref="shell.probe",
        input={"target": TARGET},
    )


def test_from_config_requires_tool() -> None:
    assert FaultInjector.from_config({}) is None
    assert FaultInjector.from_config({"error": "x"}) is None


def test_fault_injector_fails_first_n_then_passes() -> None:
    injector = FaultInjector.from_config(
        {
            "tool": "shell.probe",
            "fail_first_n": 2,
            "error": "simulated failure",
            "error_category": "tool_invalid",
        }
    )
    assert injector is not None

    first = injector.maybe_fail("shell.probe", {})
    second = injector.maybe_fail("shell.probe", {})
    third = injector.maybe_fail("shell.probe", {})

    assert first is not None
    assert first.status == "failed"
    assert first.error_category == "tool_invalid"
    assert second is not None
    assert third is None
    assert injector.calls == 3


def test_broker_loop_tool_fault_then_recovery() -> None:
    broker = ToolBroker(FakeRunner())
    injector = FaultInjector.from_config(
        {
            "tool": "shell.probe",
            "fail_first_n": 1,
            "error": "simulated scan timeout",
            "error_category": "transient",
            "retryable": True,
        }
    )
    loop_tool = BrokerLoopTool(
        broker,
        _spec(),
        fault_injector=injector,
    )
    first = loop_tool.execute(
        _proposal(),
        idempotency_key="fault:1",
    )
    second = loop_tool.execute(
        _proposal(),
        idempotency_key="fault:2",
    )

    assert first.status == "failed"
    assert first.error_category == "transient"
    assert first.retryable is True
    assert second.status == "completed"


def test_other_tools_bypass_fault_injector() -> None:
    injector = FaultInjector.from_config(
        {
            "tool": "nmap.scan",
            "fail_first_n": 1,
        }
    )
    assert injector is not None

    result = injector.maybe_fail("shell.probe", {})

    assert result is None


def test_multi_tool_fault_injection() -> None:
    injector = FaultInjector.from_config(
        {
            "tools": [
                {"tool": "a", "fail_first_n": 1},
                {"tool": "b", "fail_first_n": 2},
            ]
        }
    )
    assert injector is not None

    assert injector.maybe_fail("a", {}) is not None
    assert injector.maybe_fail("a", {}) is None
    assert injector.maybe_fail("b", {}) is not None
    assert injector.maybe_fail("b", {}) is not None
    assert injector.maybe_fail("b", {}) is None


def test_broker_loop_tool_respects_node_scope() -> None:
    broker = ToolBroker(FakeRunner())
    loop_tool = BrokerLoopTool(
        broker,
        _spec(),
        node_allowed_tools=("run.finish",),
    )

    result = loop_tool.execute(
        _proposal(),
        idempotency_key="scope:1",
    )

    assert result.status == "denied"
    assert "tool_not_in_node_scope" in result.error
