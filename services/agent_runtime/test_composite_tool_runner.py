from __future__ import annotations

import pytest

from services.agent_runtime.kernel.composite_tool_runner import CompositeToolRunner
from services.agent_runtime.kernel.contracts import ExecutionRequest, ExecutionResult


def _request(tool_ref: str) -> ExecutionRequest:
    return ExecutionRequest(
        action_id="action_1",
        run_id="run_1",
        tool_ref=tool_ref,
        input={},
        idempotency_key=f"run_1:{tool_ref}:1",
    )


def test_composite_tool_runner_dispatches_by_tool() -> None:
    class StubRunner:
        def __init__(self, marker: str) -> None:
            self.marker = marker

        def execute(self, request: ExecutionRequest) -> ExecutionResult:
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout=self.marker,
            )

    shell = StubRunner("shell")
    web = StubRunner("web")
    runner = CompositeToolRunner({"shell.probe": shell, "browser.open": web})

    assert runner.execute(_request("shell.probe")).stdout == "shell"
    assert runner.execute(_request("browser.open")).stdout == "web"


def test_composite_tool_runner_fails_closed_for_unknown_tool() -> None:
    runner = CompositeToolRunner({})
    with pytest.raises(ValueError, match="no runner"):
        runner.execute(_request("shell.probe"))


def test_composite_tool_runner_aggregates_observations() -> None:
    class StubRunner:
        def __init__(self, records) -> None:
            self._records = records

        def execute(self, request: ExecutionRequest) -> ExecutionResult:
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
            )

        def observations(self):
            return self._records

    runner = CompositeToolRunner(
        {
            "browser.open": StubRunner([{"request_id": "req_1"}]),
            "shell.probe": StubRunner([{"request_id": "req_2"}]),
        }
    )

    assert runner.observations() == [
        {"request_id": "req_1"},
        {"request_id": "req_2"},
    ]


def test_composite_tool_runner_aggregates_replay_proofs() -> None:
    class ReplayRunner:
        def replay_proofs(self):
            return {"req_1": {"matched": True}}

    runner = CompositeToolRunner({"web.replay": ReplayRunner()})

    assert runner.replay_proofs() == {"req_1": {"matched": True}}
