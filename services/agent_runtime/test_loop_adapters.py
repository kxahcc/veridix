from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    AgentRunSpec,
    CoverageRecord,
    ExecutionRequest,
    ExecutionResult,
    LoopState,
    LoopSpec,
    ModelEvent,
    OracleResult,
    ToolCall,
)
from services.agent_runtime.kernel.fake_runner import FakeRunner
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loop_adapters import (
    BrokerLoopTool,
    TurnLoopModelAdapter,
)
from services.agent_runtime.kernel.ports import OraclePort
from services.agent_runtime.kernel.tool_broker import ToolBroker
from services.agent_runtime.roles import StructuredFindingOracle


class ToolCallBackend:
    def __init__(self, call: ToolCall) -> None:
        self._call = call

    def stream(self, context):
        yield ModelEvent(type="model.delta", text="thinking")
        yield ModelEvent(
            type="tool_call",
            tool_call=self._call,
            reasoning_content="use shell",
        )


class FinishBackend:
    def stream(self, context):
        yield ModelEvent(type="model.finish", text="done")


class MultiToolCallBackend:
    def stream(self, context):
        yield ModelEvent(
            type="tool_call",
            tool_call=ToolCall(
                id="call_1",
                name="shell.probe",
                arguments={"target": "https://lab.example.test"},
            ),
        )
        yield ModelEvent(
            type="tool_call",
            tool_call=ToolCall(
                id="call_2",
                name="shell.probe",
                arguments={"target": "https://lab.example.test/admin"},
            ),
        )


class AlwaysVerifiedOracle(OraclePort):
    def evaluate(self, state, facts, coverage: CoverageRecord) -> OracleResult:
        return OracleResult(
            status="verified",
            evidence_refs=tuple(sorted({ref for fact in facts for ref in fact.source_refs})),
            reason="fixture",
        )


def _spec() -> AgentRunSpec:
    return AgentRunSpec(
        run_id="run_loop_adapter",
        mission_id="mission_1",
        target_ref="https://lab.example.test",
        behavior_snapshot="behavior_loop_adapter",
        allowed_targets=("https://lab.example.test",),
        allowed_tools=("shell.probe",),
        mission="run one probe",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )


def test_turn_adapter_and_broker_tool_execute_real_loop() -> None:
    runner = FakeRunner()
    broker = ToolBroker(runner)
    spec = _spec()
    backend = ToolCallBackend(
        ToolCall(
            id="call_1",
            name="shell.probe",
            arguments={"target": "https://lab.example.test"},
        )
    )
    loop_spec = LoopSpec(
        loop_id="loop_adapter",
        profile="web_discovery",
        max_iterations=1,
        allowed_tools=("shell.probe",),
    )
    loop = LoopRunner(
        loop_spec,
        TurnLoopModelAdapter(
            backend,
            target_ref=spec.target_ref,
            mission=spec.mission,
        ),
        BrokerLoopTool(broker, spec),
        AlwaysVerifiedOracle(),
    )

    result = loop.run()

    assert len(runner.executions) == 1
    assert result.facts[0].predicate == "observed:shell.probe"
    assert result.status == "succeeded"
    assert result.stop_reason == "oracle_verified"


def test_turn_adapter_restores_observation_history_from_state() -> None:
    adapter = TurnLoopModelAdapter(
        FinishBackend(),
        target_ref="https://lab.example.test",
        mission="resume after crash",
    )
    history = (
        {"endpoint": "/", "kind": "memory.recall"},
        {"tool": "nmap.scan", "endpoint": "10.0.0.0/24"},
    )
    state = LoopState(
        loop_id="loop_resume",
        spec_ref="loop_resume",
        observation_history=history,
    )

    decision = adapter.propose(state, {})

    assert decision.kind == "finish"
    assert adapter._observation_history == [dict(item) for item in history]


def test_broker_tool_does_not_turn_memory_calls_into_coverage_facts() -> None:
    class MemoryRunner:
        def execute(self, request: ExecutionRequest) -> ExecutionResult:
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                exit_code=0,
                stdout='{"count": 1, "facts": []}',
                observations=(
                    {
                        "kind": "memory.recall",
                        "query": "prior knowledge",
                        "count": 1,
                        "facts": [],
                    },
                ),
            )

    broker = ToolBroker(MemoryRunner())
    spec = AgentRunSpec(
        run_id="run_memory_scope",
        mission_id="mission_1",
        target_ref="https://lab.example.test",
        behavior_snapshot="behavior_memory_scope",
        allowed_targets=("https://lab.example.test",),
        allowed_tools=("memory.recall",),
        mission="recall memory",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    tool = BrokerLoopTool(broker, spec)

    result = tool.execute(
        ActionProposal(
            action_id="memory_1",
            tool_ref="memory.recall",
            input={"query": "prior knowledge"},
        ),
        idempotency_key="run_memory_scope:memory.recall:1",
    )

    assert result.status == "completed"
    assert result.facts == ()
    assert result.observations[0]["kind"] == "memory.recall"


def test_multi_tool_calls_in_one_turn_execute_and_aggregate() -> None:
    runner = FakeRunner()
    broker = ToolBroker(runner)
    spec = _spec()
    loop_spec = LoopSpec(
        loop_id="loop_multi",
        profile="web_discovery",
        max_iterations=1,
        allowed_tools=("shell.probe",),
    )
    loop = LoopRunner(
        loop_spec,
        TurnLoopModelAdapter(
            MultiToolCallBackend(),
            target_ref=spec.target_ref,
            mission=spec.mission,
        ),
        BrokerLoopTool(broker, spec),
        AlwaysVerifiedOracle(),
    )

    result = loop.run()

    assert len(runner.executions) == 2
    assert len(result.facts) == 2
    assert result.status == "succeeded"
    assert result.stop_reason == "oracle_verified"


def test_parallel_tool_calls_opt_in_executes_both() -> None:
    runner = FakeRunner()
    broker = ToolBroker(runner)
    spec = _spec()
    loop_spec = LoopSpec(
        loop_id="loop_parallel",
        profile="web_discovery",
        max_iterations=1,
        allowed_tools=("shell.probe",),
        budget={"parallel_tool_calls": True},
    )
    loop = LoopRunner(
        loop_spec,
        TurnLoopModelAdapter(
            MultiToolCallBackend(),
            target_ref=spec.target_ref,
            mission=spec.mission,
        ),
        BrokerLoopTool(broker, spec),
        AlwaysVerifiedOracle(),
    )

    result = loop.run()

    assert len(runner.executions) == 2
    assert result.status == "succeeded"
    assert result.stop_reason == "oracle_verified"


def test_broker_tool_returns_tool_invalid_with_validator() -> None:
    runner = FakeRunner()
    broker = ToolBroker(runner)
    loop_tool = BrokerLoopTool(
        broker,
        _spec(),
        argument_validator=lambda ref, args: ["missing required argument url"],
    )

    result = loop_tool.execute(
        ActionProposal(
            action_id="invalid",
            tool_ref="shell.probe",
            input={"target": "https://lab.example.test"},
            reasoning="",
        ),
        idempotency_key="invalid:1",
    )

    assert result.status == "failed"
    assert result.retryable is True
    assert "tool_invalid" in result.error
    assert len(runner.executions) == 0


def test_turn_adapter_trims_context_and_injects_summary() -> None:
    class CaptureBackend:
        def __init__(self) -> None:
            self.captured = []

        def stream(self, context):
            self.captured.append(context)
            yield ModelEvent(type="model.finish", text="done")

    backend = CaptureBackend()
    adapter = TurnLoopModelAdapter(
        backend,
        target_ref="https://lab.example.test",
        mission="scan target",
        max_context_tokens=800,
        keep_recent_observations=1,
    )
    adapter._observation_history = [
        {
            "tool": "web.nikto.scan",
            "arguments": {"target": "http://target/1"},
            "stdout": "x" * 1000,
        },
        {
            "tool": "web.nikto.scan",
            "arguments": {"target": "http://target/2"},
            "stdout": "y" * 1000,
        },
    ]

    adapter.propose(
        LoopState(
            loop_id="loop_trim",
            spec_ref="loop_trim",
            iteration=1,
        ),
        {},
    )

    context = backend.captured[0]
    assert len(context.observations) == 1
    assert context.observations[0]["arguments"]["target"] == (
        "http://target/2"
    )
    assert any(
        "Trimmed earlier observations" in item
        for item in context.context_blocks.summaries
    )


def test_turn_adapter_keeps_heuristic_summary_when_model_summarizer_fails() -> None:
    class CaptureBackend:
        def __init__(self) -> None:
            self.captured = []

        def stream(self, context):
            self.captured.append(context)
            yield ModelEvent(type="model.finish", text="done")

    class FailingSummarizer:
        def __call__(self, observations):
            raise RuntimeError("model unavailable")

    backend = CaptureBackend()
    adapter = TurnLoopModelAdapter(
        backend,
        target_ref="https://lab.example.test",
        mission="scan target",
        max_context_tokens=800,
        keep_recent_observations=1,
        summarizer=FailingSummarizer(),
    )
    adapter._observation_history = [
        {
            "tool": "web.nikto.scan",
            "arguments": {"target": "http://target/1"},
            "stdout": "x" * 1000,
        },
        {
            "tool": "web.nikto.scan",
            "arguments": {"target": "http://target/2"},
            "stdout": "y" * 1000,
        },
    ]

    adapter.propose(
        LoopState(
            loop_id="loop_trim_fallback",
            spec_ref="loop_trim_fallback",
            iteration=1,
        ),
        {},
    )

    context = backend.captured[0]
    assert any(
        "Trimmed earlier observations" in item
        for item in context.context_blocks.summaries
    )


def test_turn_adapter_uses_observed_token_usage_to_compact() -> None:
    class UsageBackend:
        last_usage = {"prompt_tokens": 1_000_000}

        def __init__(self) -> None:
            self.captured = []

        def stream(self, context):
            self.captured.append(context)
            yield ModelEvent(type="model.finish", text="done")

    backend = UsageBackend()
    adapter = TurnLoopModelAdapter(
        backend,
        target_ref="https://lab.example.test",
        mission="scan target",
        max_context_tokens=8_000,
        keep_recent_observations=2,
    )
    adapter._observation_history = [
        {
            "tool": "web.nikto.scan",
            "arguments": {"target": f"http://target/{index}"},
            "stdout": "x" * 200,
        }
        for index in range(10)
    ]

    adapter.propose(
        LoopState(
            loop_id="loop_usage",
            spec_ref="loop_usage",
            iteration=1,
        ),
        {},
    )

    assert len(backend.captured[0].observations) == 2


def test_broker_tool_injects_run_target_when_proposal_omits_it() -> None:
    runner = FakeRunner()
    broker = ToolBroker(runner)
    spec = _spec()
    loop_tool = BrokerLoopTool(broker, spec)

    result = loop_tool.execute(
        ActionProposal(
            action_id="a_inject",
            tool_ref="shell.probe",
            input={},
            reasoning="probe",
        ),
        idempotency_key="k_inject",
    )

    assert result.status == "completed"
    assert len(runner.executions) == 1
    assert runner.executions[0].input["target"] == spec.target_ref
    assert runner.executions[0].input["url"] == spec.target_ref


def test_turn_adapter_finish_stops_loop() -> None:
    broker = ToolBroker(FakeRunner())
    spec = _spec()
    loop = LoopRunner(
        LoopSpec(
            loop_id="loop_finish",
            profile="verifier",
            max_iterations=2,
        ),
        TurnLoopModelAdapter(
            FinishBackend(),
            target_ref=spec.target_ref,
            mission=spec.mission,
        ),
        BrokerLoopTool(broker, spec),
        AlwaysVerifiedOracle(),
    )

    result = loop.run()

    assert result.status == "succeeded"
    assert result.stop_reason == "oracle_verified"


def test_broker_tool_creates_finding_facts_from_structured_output() -> None:
    class FindingRunner:
        def execute(self, request):
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout="{}",
                artifact_refs=("artifact://scan/1",),
                observations=(
                    {
                        "endpoint": "/admin",
                        "vuln_category": "XSS",
                        "matched_at": "/admin?q=1",
                        "template_id": "xss-template",
                    },
                ),
            )

    broker = ToolBroker(FindingRunner())
    spec = _spec()
    loop = LoopRunner(
        LoopSpec(
            loop_id="loop_finding",
            profile="verifier",
            max_iterations=1,
            allowed_tools=("shell.probe",),
        ),
        TurnLoopModelAdapter(
            ToolCallBackend(
                ToolCall(
                    id="call_finding",
                    name="shell.probe",
                    arguments={"target": "https://lab.example.test"},
                )
            ),
            target_ref=spec.target_ref,
            mission=spec.mission,
        ),
        BrokerLoopTool(broker, spec),
        StructuredFindingOracle(required_categories=("XSS",)),
    )

    result = loop.run()

    assert result.status == "succeeded"
    assert any(
        fact.predicate == "finding" and fact.value == "XSS"
        for fact in result.facts
    )
    finding = next(
        fact for fact in result.facts if fact.predicate == "finding"
    )
    assert finding.metadata["vuln_category"] == "XSS"


def test_broker_tool_merges_mission_tool_args_defaults() -> None:
    class RecordingRunner:
        def __init__(self) -> None:
            self.request = None

        def execute(self, request):
            self.request = request
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout="",
            )

    runner = RecordingRunner()
    broker = ToolBroker(runner)
    spec = AgentRunSpec(
        run_id="run_sqlmap_defaults",
        mission_id="mission_1",
        target_ref="http://lab.example.test",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://lab.example.test",),
        allowed_tools=("web.sqlmap.scan",),
        mission="scan with defaults",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    loop_tool = BrokerLoopTool(
        broker,
        spec,
        tool_args={
            "web.sqlmap.scan": {"cookie": "PHPSESSID=abc"},
        },
    )

    loop_tool.execute(
        ActionProposal(
            action_id="action_sqlmap",
            tool_ref="web.sqlmap.scan",
            input={"url": "http://lab.example.test/sqli"},
        ),
        idempotency_key="run_loop_adapter:1",
    )

    assert runner.request.input == {
        "url": "http://lab.example.test/sqli",
        "cookie": "PHPSESSID=abc",
    }


def test_broker_tool_forced_args_override_model_input() -> None:
    class RecordingRunner:
        def __init__(self) -> None:
            self.request = None

        def execute(self, request):
            self.request = request
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout="",
            )

    runner = RecordingRunner()
    broker = ToolBroker(runner)
    spec = AgentRunSpec(
        run_id="run_owasp_forced",
        mission_id="mission_1",
        target_ref="http://wordpress.test",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://wordpress.test",),
        allowed_tools=("web.owasp.test",),
        mission="force rate-limit check",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    loop_tool = BrokerLoopTool(
        broker,
        spec,
        tool_args={
            "web.owasp.test": {
                "login_path": "/wp-login.php",
            },
        },
        forced_tool_args={
            "web.owasp.test": {
                "check": "rate_limit",
                "login_path": "/wp-login.php",
            },
        },
    )

    loop_tool.execute(
        ActionProposal(
            action_id="action_owasp_forced",
            tool_ref="web.owasp.test",
            input={
                "target": "http://wordpress.test",
                "check": "security_headers",
                "login_path": "/login.php",
            },
        ),
        idempotency_key="run_loop_adapter:forced",
    )

    assert runner.request.input["check"] == "rate_limit"
    assert runner.request.input["login_path"] == "/wp-login.php"


def test_broker_tool_merges_native_tester_path_arguments() -> None:
    class RecordingRunner:
        def __init__(self) -> None:
            self.request = None

        def execute(self, request):
            self.request = request
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout="",
            )

    runner = RecordingRunner()
    broker = ToolBroker(runner)
    spec = AgentRunSpec(
        run_id="run_owasp_paths",
        mission_id="mission_1",
        target_ref="http://wordpress.test",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://wordpress.test",),
        allowed_tools=("web.owasp.test",),
        mission="check wordpress headers",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    loop_tool = BrokerLoopTool(
        broker,
        spec,
        tool_args={
            "web.owasp.test": {"login_path": "/wp-login.php"},
        },
    )

    loop_tool.execute(
        ActionProposal(
            action_id="action_owasp",
            tool_ref="web.owasp.test",
            input={
                "target": "http://wordpress.test",
                "check": "security_headers",
            },
        ),
        idempotency_key="run_loop_adapter:2",
    )

    assert runner.request.input["login_path"] == "/wp-login.php"
    assert runner.request.input["check"] == "security_headers"


def test_broker_tool_handles_run_finish_natively() -> None:
    class ExplodingRunner:
        def execute(self, request):
            raise AssertionError("run.finish must not reach the runner")

    broker = ToolBroker(ExplodingRunner())
    spec = AgentRunSpec(
        run_id="run_finish_native",
        mission_id="mission_1",
        target_ref="http://lab.example.test",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://lab.example.test",),
        allowed_tools=("nmap.scan", "run.finish"),
        mission="finish natively",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    loop_tool = BrokerLoopTool(broker, spec)

    result = loop_tool.execute(
        ActionProposal(
            action_id="action_finish",
            tool_ref="run.finish",
            input={"summary": "done"},
        ),
        idempotency_key="run_finish_native:1",
    )

    assert result.status == "finished"
    assert result.observations == ()


def test_broker_tool_round_trips_reasoning_content() -> None:
    class RecordingRunner:
        def execute(self, request):
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout="probe done",
            )

    runner = RecordingRunner()
    broker = ToolBroker(runner)
    spec = _spec()
    loop_tool = BrokerLoopTool(broker, spec)

    result = loop_tool.execute(
        ActionProposal(
            action_id="action_reason",
            tool_ref="shell.probe",
            input={"target": "https://lab.example.test"},
            reasoning="first I probe, then I verify",
        ),
        idempotency_key="run_reason:1",
    )

    assert result.status == "completed"
    assert any(
        observation.get("reasoning_content")
        == "first I probe, then I verify"
        for observation in result.observations
    )


def test_broker_tool_creates_callback_and_ssrf_facts() -> None:
    class CallbackRunner:
        def execute(self, request):
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout="{}",
                artifact_refs=("artifact://oast/1",),
                observations=(
                    {
                        "kind": "oast_callback",
                        "token": "oast_abc",
                        "callback_id": "cb_1",
                        "source": "http",
                    },
                ),
            )

    broker = ToolBroker(CallbackRunner())
    spec = AgentRunSpec(
        run_id="run_callback",
        mission_id="mission_1",
        target_ref="https://lab.example.test",
        behavior_snapshot="behavior_1",
        allowed_targets=("https://lab.example.test",),
        allowed_tools=("oast.check",),
        mission="check callback",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    tool = BrokerLoopTool(broker, spec)

    result = tool.execute(
        ActionProposal(
            action_id="oast_check_1",
            tool_ref="oast.check",
            input={"token": "oast_abc"},
        ),
        idempotency_key="run:oast.check:1",
    )

    assert any(
        fact.predicate == "callback_evidence"
        and fact.value == "verified"
        for fact in result.facts
    )
    assert any(
        fact.predicate == "finding" and fact.value == "SSRF"
        for fact in result.facts
    )
