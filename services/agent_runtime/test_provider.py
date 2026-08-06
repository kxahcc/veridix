from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from services.agent_runtime.kernel.contracts import (
    AgentRunSpec,
    ContextView,
    RunStatus,
    ScriptItem,
    ToolCall,
)
from services.agent_runtime.kernel.fake_model import FakeModelAdapter
from services.agent_runtime.kernel.fake_runner import FakeRunner
from services.agent_runtime.kernel.kernel import AgentKernel
from services.agent_runtime.kernel.memory import (
    InMemoryCheckpointStore,
    InMemoryEventSink,
)
from services.agent_runtime.kernel.tool_broker import ToolBroker
from services.agent_runtime.provider.openai_adapter import (
    OpenAICompatibleTurnBackend,
    ProviderError,
    _parse_retry_after,
)
from services.agent_runtime.provider.sdk_adapter import SdkTurnBackend
from services.agent_runtime.provider.sdk_adapter import _get_event_loop

RUN_ID = "run_provider_001"
TARGET = "https://lab.example.test"


class MockOpenAIHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:
        if not self.path.endswith("/chat/completions"):
            self._send(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).calls += 1
        if type(self).calls == 1:
            payload = {
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "created": 1,
                "model": body.get("model", "fixture-model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "shell.probe",
                                        "arguments": json.dumps({"target": TARGET}),
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        else:
            payload = {
                "id": "chatcmpl_2",
                "object": "chat.completion",
                "created": 2,
                "model": body.get("model", "fixture-model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "probe complete",
                        },
                    }
                ],
            }
        self._send(200, payload)

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


@pytest.fixture()
def mock_openai():
    MockOpenAIHandler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def make_kernel(backend):
    spec = AgentRunSpec(
        run_id=RUN_ID,
        mission_id="mission_provider_001",
        target_ref=TARGET,
        behavior_snapshot="behavior_provider_001",
        allowed_targets=(TARGET,),
        allowed_tools=("shell.probe", "run.finish"),
        max_turns=5,
    )
    runner = FakeRunner()
    broker = ToolBroker(runner)
    events = InMemoryEventSink()
    checkpoints = InMemoryCheckpointStore()
    kernel = AgentKernel(spec, backend, broker, events, checkpoints)
    return kernel, runner


def test_openai_compatible_backend_runs_reference_fixture(mock_openai: str) -> None:
    backend = OpenAICompatibleTurnBackend(
        base_url=mock_openai,
        model="fixture-model",
        timeout_seconds=5,
    )
    kernel, runner = make_kernel(backend)

    kernel.start()
    status = kernel.submit("find an exposed admin panel")

    assert status == RunStatus.SUCCEEDED
    assert len(runner.executions) == 1
    assert runner.executions[0].input["target"] == TARGET


def test_openai_backend_empty_tool_schemas_stays_empty() -> None:
    backend = OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        tool_schemas={},
        timeout_seconds=1,
    )

    assert backend._tool_schemas == {}


def test_sdk_event_loop_is_reused() -> None:
    first = _get_event_loop()
    second = _get_event_loop()

    assert first is second


def test_openai_json_mode_retries_when_model_returns_non_json(
    monkeypatch,
) -> None:
    backend = OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        json_mode=True,
        retries=3,
        timeout_seconds=1,
    )
    calls: list[dict] = []

    def fake_create(request_kwargs: dict) -> SimpleNamespace:
        calls.append(request_kwargs)
        if len(calls) == 1:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="   ",
                            tool_calls=None,
                            reasoning_content="thinking only",
                        )
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=2,
                    total_tokens=12,
                ),
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"ok": true}',
                        tool_calls=None,
                        reasoning_content="",
                    )
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=12,
                completion_tokens=4,
                total_tokens=16,
            ),
        )

    monkeypatch.setattr(backend, "_create_with_retry", fake_create)
    context = ContextView(
        mission="return json",
        target_ref=TARGET,
        observations=(),
        remaining_budget=5,
    )

    events = list(backend.stream(context))

    finish = [event for event in events if event.type == "model.finish"]
    assert len(calls) == 2
    assert finish[0].payload == {"json": {"ok": True}}
    assert "Return only a valid JSON object" in calls[1]["messages"][-1]["content"]


def test_provider_failure_is_classified() -> None:
    backend = OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        timeout_seconds=1,
    )
    context = ContextView(
        mission="probe",
        target_ref=TARGET,
        observations=(),
        remaining_budget=5,
    )

    with pytest.raises(ProviderError) as exc_info:
        list(backend.stream(context))

    assert exc_info.value.category in ("provider_unavailable", "provider_timeout")
    assert str(exc_info.value)


def test_rate_limit_retry_after_is_classified(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs) -> object:
            captured.update(kwargs)

            class FakeRateLimitError(RuntimeError):
                def __init__(self) -> None:
                    super().__init__("rate limited")
                    self.response = type(
                        "FakeResponse",
                        (),
                        {"headers": {"retry-after": "5"}},
                    )()
                    self.status_code = 429

            raise FakeRateLimitError()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            pass

        chat = FakeChat()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    backend = OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        timeout_seconds=1,
    )
    context = ContextView(
        mission="probe",
        target_ref=TARGET,
        observations=(),
        remaining_budget=5,
    )

    with pytest.raises(ProviderError) as exc_info:
        list(backend.stream(context))

    assert exc_info.value.category == "provider_rate_limit"
    assert exc_info.value.retry_after_seconds == 5.0
    assert _parse_retry_after(None) is None


def test_openai_backend_resolves_env_api_key_ref(monkeypatch) -> None:
    monkeypatch.setenv("VERIDIX_TEST_API_KEY", "sk-test-123")
    captured: dict[str, str] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        api_key="env:VERIDIX_TEST_API_KEY",
        timeout_seconds=1,
    )

    assert captured["api_key"] == "sk-test-123"


def test_openai_backend_falls_back_when_env_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("VERIDIX_MISSING_KEY", raising=False)
    captured: dict[str, str] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        api_key="env:VERIDIX_MISSING_KEY",
        timeout_seconds=1,
    )

    assert captured["api_key"] == "probe-only"


def test_openai_backend_projects_tool_names_for_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeToolCall:
        id = "call_1"

        class Function:
            name = "shell_probe"
            arguments = json.dumps({"target": TARGET})

        function = Function()

    class FakeMessage:
        content = None
        tool_calls = [FakeToolCall()]
        reasoning_content = "think step by step"

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        def create(self, **kwargs) -> object:
            captured.update(kwargs)

            class FakeResponse:
                choices = [FakeChoice()]

            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            pass

        chat = FakeChat()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    backend = OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        timeout_seconds=1,
    )
    context = ContextView(
        mission="probe",
        target_ref=TARGET,
        observations=(),
        remaining_budget=5,
    )

    events = list(backend.stream(context))

    assert captured["tools"][0]["function"]["name"] == "shell_probe"
    assert events[0].tool_call is not None
    assert events[0].tool_call.name == "shell.probe"
    assert events[0].reasoning_content == "think step by step"


def test_openai_backend_exposes_configurable_provider_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs) -> object:
            captured.update(kwargs)

            class FakeMessage:
                content = "done"
                tool_calls = None

            class FakeChoice:
                message = FakeMessage()

            class FakeResponse:
                choices = [FakeChoice()]

            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            pass

        chat = FakeChat()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    backend = OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        timeout_seconds=1,
        thinking_mode="enabled",
        tool_choice="auto",
    )
    context = ContextView(
        mission="probe",
        target_ref=TARGET,
        observations=(),
        remaining_budget=5,
    )

    list(backend.stream(context))

    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert captured["tool_choice"] == "auto"


def test_openai_backend_emits_finish_for_text_only_completion(monkeypatch) -> None:
    class FakeCompletions:
        def create(self, **kwargs) -> object:
            class FakeMessage:
                content = "probe complete"
                tool_calls = None
                reasoning_content = "checked the target"

            class FakeChoice:
                message = FakeMessage()

            class FakeResponse:
                choices = [FakeChoice()]

            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            pass

        chat = FakeChat()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    backend = OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        timeout_seconds=1,
    )
    context = ContextView(
        mission="probe and finish",
        target_ref=TARGET,
        observations=(),
        remaining_budget=5,
    )

    events = list(backend.stream(context))

    assert [event.type for event in events] == ["model.finish"]
    assert events[0].text == "probe complete"
    assert events[0].reasoning_content == "checked the target"


def test_openai_backend_streams_deltas_and_tool_call(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeToolDelta:
        def __init__(self, index, part) -> None:
            self.index = index
            self.id = "call_stream"
            self.function = type(
                "Function",
                (),
                {"name": part.get("name"), "arguments": part.get("arguments")},
            )()

    class FakeDelta:
        def __init__(self, content=None, tool_call=None) -> None:
            self.content = content
            self.tool_calls = [tool_call] if tool_call else None
            self.reasoning_content = None

    class FakeChoice:
        def __init__(self, delta) -> None:
            self.delta = delta

    class FakeChunk:
        def __init__(self, delta) -> None:
            self.choices = [FakeChoice(delta)]

    stream = [
        FakeChunk(FakeDelta(content="probe ")),
        FakeChunk(FakeDelta(content="complete")),
        FakeChunk(
            FakeDelta(
                tool_call=FakeToolDelta(0, {"name": "shell_", "arguments": "{\"t"})
            )
        ),
        FakeChunk(
            FakeDelta(
                tool_call=FakeToolDelta(0, {"name": "probe", "arguments": "arget\": \"x\"}"})
            )
        ),
    ]

    class FakeCompletions:
        def create(self, **kwargs) -> object:
            captured.update(kwargs)
            return stream

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            pass

        chat = FakeChat()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    backend = OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        timeout_seconds=1,
        streaming=True,
    )
    context = ContextView(
        mission="probe",
        target_ref=TARGET,
        observations=(),
        remaining_budget=5,
    )

    events = list(backend.stream(context))

    assert captured["stream"] is True
    types = [event.type for event in events]
    assert types == ["model.delta", "model.delta", "model.tool_call"]
    assert events[0].text == "probe "
    assert events[2].tool_call is not None
    assert events[2].tool_call.name == "shell.probe"
    assert events[2].tool_call.arguments == {"target": "x"}


def test_openai_backend_includes_target_scope_in_prompt() -> None:
    backend = OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        timeout_seconds=1,
    )
    context = ContextView(
        mission="probe the target once",
        target_ref=TARGET,
        observations=(),
        remaining_budget=5,
    )

    messages = backend._build_messages(context)

    assert TARGET in messages[-1]["content"]
    assert "probe the target once" in messages[-1]["content"]


def test_openai_backend_builds_tool_result_history() -> None:
    backend = OpenAICompatibleTurnBackend(
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        timeout_seconds=1,
    )
    context = ContextView(
        mission="probe and finish",
        target_ref=TARGET,
        observations=(
            {
                "tool": "shell.probe",
                "arguments": {"target": TARGET},
                "tool_call_id": "call_probe_1",
                "reasoning_content": "think step by step",
                "stdout": "probe:ok",
                "artifact_refs": ["artifact://action_1/stdout"],
                "replayed": False,
            },
        ),
        remaining_budget=3,
    )

    messages = backend._build_messages(context)

    assistant = messages[-2]
    tool_result = messages[-1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"]["name"] == "shell_probe"
    assert assistant["reasoning_content"] == "think step by step"
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "call_probe_1"
    assert tool_result["content"] == "probe:ok"


def test_sdk_backend_runs_reference_fixture() -> None:
    pytest.importorskip("agents")

    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import (
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
    )
    from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

    class ScriptedSdkModel(Model):
        def __init__(self) -> None:
            self.calls = 0

        async def get_response(self, *args, **kwargs) -> ModelResponse:
            self.calls += 1
            usage = Usage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                input_tokens_details=InputTokensDetails(cached_tokens=0),
                output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
            )
            if self.calls == 1:
                return ModelResponse(
                    output=[
                        ResponseFunctionToolCall(
                            id="call_1",
                            call_id="call_1",
                            name="shell.probe",
                            arguments=json.dumps({"target": TARGET}),
                            type="function_call",
                        )
                    ],
                    usage=usage,
                    response_id="r1",
                )
            return ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_2",
                        content=[
                            ResponseOutputText(
                                text="probe complete",
                                type="output_text",
                                annotations=[],
                            )
                        ],
                        role="assistant",
                        status="completed",
                        type="message",
                    )
                ],
                usage=usage,
                response_id="r2",
            )

        async def stream_response(self, *args, **kwargs):
            return
            yield  # pragma: no cover

    kernel, runner = make_kernel(SdkTurnBackend(ScriptedSdkModel()))

    kernel.start()
    status = kernel.submit("find an exposed admin panel")

    assert status == RunStatus.SUCCEEDED
    assert len(runner.executions) == 1
