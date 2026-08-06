from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    AgentRunSpec,
    CoverageRecord,
    LoopState,
    LoopSpec,
    LoopToolResult,
    ModelEvent,
    OracleResult,
    RunStatus,
    ToolCall,
)
from services.agent_runtime.kernel.fake_runner import FakeRunner
from services.agent_runtime.kernel.kernel import AgentKernel
from services.agent_runtime.kernel.memory import (
    InMemoryCheckpointStore,
    InMemoryEventSink,
)
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loops import (
    ScriptedLoopModel,
    action,
    finish,
)
from services.agent_runtime.kernel.tool_broker import ToolBroker


TARGET = "https://lab.example.test"


class AlwaysVerified:
    def evaluate(self, state, facts, coverage: CoverageRecord) -> OracleResult:
        return OracleResult(status="verified", reason="fixture")


class KeepRunningOracle:
    def evaluate(self, state, facts, coverage: CoverageRecord) -> OracleResult:
        return OracleResult(status="inconclusive", reason="keep_running")


def _proposal(tool: str = "shell.probe", index: int = 1) -> ActionProposal:
    return ActionProposal(
        action_id=f"a{index}",
        tool_ref=tool,
        input={"target": "https://lab.example.test", "index": index},
    )


class TransientTool:
    def __init__(self, failures: int = 2) -> None:
        self.calls = 0
        self.failures = failures

    def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
        self.calls += 1
        if self.calls <= self.failures:
            return LoopToolResult(
                status="failed",
                error="transient failure",
                error_category="transient",
                retryable=True,
            )
        return LoopToolResult(
            status="completed",
            observations=[{"endpoint": "/"}],
            evidence_refs=("evidence://probe",),
        )


def _loop(
    spec: LoopSpec,
    tool,
    script,
    oracle=None,
) -> LoopRunner:
    return LoopRunner(
        spec,
        ScriptedLoopModel(script),
        tool,
        oracle or KeepRunningOracle(),
    )


def test_transient_failures_retry_and_metrics_are_recorded() -> None:
    spec = LoopSpec(
        loop_id="loop_retry",
        profile="web_discovery",
        max_iterations=3,
        allowed_tools=("shell.probe",),
        budget={"retry_transient": 2, "wall_clock_seconds": -1},
    )
    tool = TransientTool()
    runner = _loop(
        spec,
        tool,
        [action(_proposal()), finish("done")],
        oracle=AlwaysVerified(),
    )

    result = runner.run()

    assert result.status == "succeeded"
    assert tool.calls == 3
    assert result.metrics is not None
    assert result.metrics.retries == 2
    assert result.metrics.tool_errors == 2
    assert result.metrics.tool_calls == 1
    assert result.metrics.verified_result is True
    assert result.metrics.tool_selection_accuracy == 1.0


def test_environment_unavailable_stops_waiting() -> None:
    class EnvTool:
        def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
            return LoopToolResult(
                status="failed",
                error="runner offline",
                error_category="environment_unavailable",
            )

    runner = _loop(
        LoopSpec(
            loop_id="loop_env",
            profile="host",
            max_iterations=3,
            allowed_tools=("shell.probe",),
        ),
        EnvTool(),
        [action(_proposal())],
    )

    result = runner.run()

    assert result.status == "waiting"
    assert result.stop_reason == "environment_unavailable"


def test_oracle_failed_becomes_inconclusive() -> None:
    class OracleFailedTool:
        def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
            return LoopToolResult(
                status="failed",
                error="oracle could not verify",
                error_category="oracle_failed",
            )

    runner = _loop(
        LoopSpec(
            loop_id="loop_oracle",
            profile="verifier",
            max_iterations=3,
            allowed_tools=("evidence.replay",),
        ),
        OracleFailedTool(),
        [action(_proposal("evidence.replay"))],
    )

    result = runner.run()

    assert result.status == "inconclusive"
    assert result.stop_reason == "oracle_failed"


def test_policy_denied_is_counted_and_not_retried() -> None:
    class DenyTool:
        def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
            return LoopToolResult(
                status="denied",
                error="tool_not_in_projection",
            )

    runner = _loop(
        LoopSpec(
            loop_id="loop_deny",
            profile="web_discovery",
            max_iterations=2,
            allowed_tools=(),
        ),
        DenyTool(),
        [action(_proposal(index=1)), action(_proposal(index=2))],
    )

    result = runner.run()

    assert result.metrics is not None
    assert result.metrics.denied == 2


def test_no_progress_guard_stops_without_crippling_retries() -> None:
    class NoProgressTool:
        def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
            return LoopToolResult(status="completed")

    runner = _loop(
        LoopSpec(
            loop_id="loop_no_progress",
            profile="web_discovery",
            max_iterations=20,
            allowed_tools=("shell.probe",),
            budget={"max_no_progress_iterations": 4},
        ),
        NoProgressTool(),
        [
            action(_proposal(index=index))
            for index in range(1, 11)
        ],
    )

    result = runner.run()

    assert result.status == "inconclusive"
    assert result.stop_reason == "no_progress"
    assert result.metrics is not None
    assert result.metrics.replan_count >= 1


def test_strict_tool_budget_stops_and_relaxed_continues() -> None:
    class OkTool:
        def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
            return LoopToolResult(
                status="completed",
                observations=[{"endpoint": f"/{proposal.action_id}"}],
            )

    strict = _loop(
        LoopSpec(
            loop_id="loop_strict",
            profile="web_discovery",
            max_iterations=5,
            allowed_tools=("shell.probe",),
            budget={"tool_calls": 2, "policy": "strict"},
        ),
        OkTool(),
        [
            action(_proposal(index=1)),
            action(_proposal(index=2)),
            action(_proposal(index=3)),
        ],
    )
    strict_result = strict.run()

    assert strict_result.stop_reason == "budget_exhausted"
    assert strict_result.metrics is not None
    assert strict_result.metrics.tool_calls == 2

    relaxed = _loop(
        LoopSpec(
            loop_id="loop_relaxed",
            profile="web_discovery",
            max_iterations=3,
            allowed_tools=("shell.probe",),
            budget={"tool_calls": 1, "policy": "relaxed"},
        ),
        OkTool(),
        [
            action(_proposal(index=1)),
            action(_proposal(index=2)),
            action(_proposal(index=3)),
        ],
    )
    relaxed_result = relaxed.run()

    assert relaxed_result.metrics is not None
    assert relaxed_result.metrics.tool_calls == 3
    assert relaxed_result.stop_reason == "budget_exhausted"


def test_kernel_turn_budget_pauses_instead_of_failing() -> None:
    class ToolCallOnly:
        def stream(self, context):
            yield ModelEvent(
                type="model.tool_call",
                tool_call=ToolCall(
                    id="call_1",
                    name="shell.probe",
                    arguments={"target": TARGET},
                ),
            )

    spec = AgentRunSpec(
        run_id="run_budget_pause",
        mission_id="mission_1",
        target_ref=TARGET,
        behavior_snapshot="behavior_1",
        allowed_targets=(TARGET,),
        allowed_tools=("shell.probe",),
        max_turns=2,
    )
    runner = FakeRunner()
    broker = ToolBroker(runner)
    events = InMemoryEventSink()
    kernel = AgentKernel(
        spec,
        ToolCallOnly(),
        broker,
        events,
        InMemoryCheckpointStore(),
    )

    kernel.start()
    status = kernel.submit("probe")

    assert status == RunStatus.PAUSED
    event_types = [event.event_type for event in events.replay(spec.run_id)]
    assert "run.budget_exhausted" in event_types


def test_duplicate_action_without_progress_is_skipped() -> None:
    class RecordingTool:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
            self.calls += 1
            return LoopToolResult(
                status="completed",
                observations=[{"endpoint": "/"}],
                evidence_refs=(),
            )

    tool = RecordingTool()
    spec = LoopSpec(
        loop_id="loop_dup",
        profile="web_discovery",
        max_iterations=5,
        allowed_tools=("shell.probe",),
    )
    runner = _loop(
        spec,
        tool,
        [
            action(_proposal(index=1)),
            action(_proposal(index=1)),
            finish("done"),
        ],
    )

    result = runner.run()

    assert tool.calls == 1
    assert any(
        event.event_type == "loop.action.duplicate_skipped"
        for event in runner.events
    )
    assert result.status in ("inconclusive", "succeeded", "failed")


def test_tool_failure_feedback_reaches_model_state() -> None:
    class InvalidTool:
        def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
            return LoopToolResult(
                status="failed",
                error="missing required field: url",
                error_category="tool_invalid",
            )

    class RecordingModel:
        def __init__(self) -> None:
            self.states: list[LoopState] = []

        def propose(self, state: LoopState, context: dict) -> ModelDecision:
            self.states.append(state)
            if len(self.states) == 1:
                return action(_proposal())
            return finish("done")

    spec = LoopSpec(
        loop_id="loop_feedback",
        profile="web_discovery",
        max_iterations=3,
        allowed_tools=("shell.probe",),
    )
    model = RecordingModel()
    runner = LoopRunner(
        spec,
        model,
        InvalidTool(),
        KeepRunningOracle(),
    )

    runner.run()

    assert len(model.states) >= 2
    feedback = model.states[1].last_tool_observations
    assert feedback
    assert feedback[0]["status"] == "failed"
    assert feedback[0]["error_category"] == "tool_invalid"
    assert "tool schema" in feedback[0]["guidance"]


def test_repeated_tool_failure_suggests_replan() -> None:
    class AlwaysInvalidTool:
        def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
            return LoopToolResult(
                status="failed",
                error="bad arguments",
                error_category="tool_invalid",
            )

    runner = _loop(
        LoopSpec(
            loop_id="loop_replan",
            profile="web_discovery",
            max_iterations=4,
            allowed_tools=("shell.probe",),
            budget={"tool_failure_replan_threshold": 2},
        ),
        AlwaysInvalidTool(),
        [
            action(_proposal(index=1)),
            action(_proposal(index=2)),
            action(_proposal(index=3)),
            finish("done"),
        ],
    )

    runner.run()

    replans = [
        event
        for event in runner.events
        if event.event_type == "loop.replan.suggested"
        and event.payload.get("reason") == "tool_repeated_failure"
    ]
    assert replans
    assert replans[0].payload["tool"] == "shell.probe"
    assert runner._replans >= 1
