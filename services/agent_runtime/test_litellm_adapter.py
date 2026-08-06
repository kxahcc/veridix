from __future__ import annotations

from types import SimpleNamespace

import litellm

from services.agent_runtime.control_worker import (
    _ActiveRun,
    ControlPlaneRunWorker,
    WorkerOptions,
    _create_turn_backend,
)
from services.agent_runtime.kernel.contracts import AgentRunSpec, ContextView
from services.agent_runtime.provider.litellm_adapter import (
    LiteLLMTurnBackend,
)
from services.agent_runtime.provider.openai_adapter import build_turn_messages


def _context() -> ContextView:
    return ContextView(
        mission="test",
        target_ref="https://lab.example.test",
        observations=(),
        remaining_budget=5,
    )


def test_litellm_backend_emits_tool_call(monkeypatch) -> None:
    def fake_completion(**kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                function=SimpleNamespace(
                                    name="code_sast_semgrep",
                                    arguments='{"path": "/workspace/input"}',
                                ),
                            )
                        ],
                    )
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
        )

    monkeypatch.setattr(litellm, "completion", fake_completion)
    backend = LiteLLMTurnBackend(
        model="deepseek/deepseek-chat",
        tool_schemas={
            "code.sast.semgrep": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            }
        },
    )

    events = list(backend.stream(_context()))

    tool_calls = [event for event in events if event.tool_call is not None]
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_call.name == "code.sast.semgrep"
    assert backend.last_usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_create_turn_backend_selects_litellm() -> None:
    backend = _create_turn_backend(
        endpoint="",
        model="deepseek/deepseek-chat",
        api_key_ref=None,
        tool_schemas={},
        tool_descriptions={},
        provider_config={"backend": "litellm"},
        options=WorkerOptions(),
    )

    assert isinstance(backend, LiteLLMTurnBackend)


def test_create_turn_backend_honors_max_tokens() -> None:
    override = _create_turn_backend(
        endpoint="",
        model="deepseek/deepseek-chat",
        api_key_ref=None,
        tool_schemas={},
        tool_descriptions={},
        provider_config={
            "backend": "litellm",
            "max_tokens": 4096,
        },
        options=WorkerOptions(max_tokens=1024),
    )
    fallback = _create_turn_backend(
        endpoint="",
        model="deepseek/deepseek-chat",
        api_key_ref=None,
        tool_schemas={},
        tool_descriptions={},
        provider_config={"backend": "litellm"},
        options=WorkerOptions(max_tokens=8192),
    )

    assert override._max_tokens == 4096
    assert fallback._max_tokens == 8192


def test_build_turn_messages_json_mode_injects_json_instruction() -> None:
    messages = build_turn_messages(_context(), json_mode=True)
    system_text = " ".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "system"
    )

    assert "JSON" in system_text
    assert "single valid JSON object" in system_text


def test_json_backend_uses_run_provider_api_key_ref(monkeypatch) -> None:
    monkeypatch.setenv("TEST_VERIDIX_KEY", "sk-run-key")
    worker = ControlPlaneRunWorker(
        client=None,
        options=WorkerOptions(),
    )
    spec = AgentRunSpec(
        run_id="run_judge",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("nmap.scan",),
        provider_model="deepseek-v4-flash",
        provider_endpoint="https://api.deepseek.com/v1",
    )
    active = _ActiveRun(
        run_id="run_judge",
        agent_spec=spec,
        kernel=None,
        runner=None,
        provider_config={"backend": "litellm"},
        provider_api_key_ref="env:TEST_VERIDIX_KEY",
    )

    backend = worker._json_backend_for(active)

    assert backend._api_key == "sk-run-key"


def test_litellm_json_mode_parses_finish_content(monkeypatch) -> None:
    def fake_completion(**kwargs):
        assert kwargs.get("response_format") == {"type": "json_object"}
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"ok": true, "priority": "high"}',
                        reasoning_content=None,
                        tool_calls=None,
                    )
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=3,
                completion_tokens=4,
                total_tokens=7,
            ),
        )

    monkeypatch.setattr(litellm, "completion", fake_completion)
    backend = LiteLLMTurnBackend(
        model="deepseek/deepseek-chat",
        json_mode=True,
    )

    events = list(backend.stream(_context()))

    finish = [event for event in events if event.type == "model.finish"]
    assert finish[0].payload == {
        "json": {"ok": True, "priority": "high"}
    }


def test_litellm_json_mode_retries_when_model_returns_non_json(
    monkeypatch,
) -> None:
    calls: list[dict] = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
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

    monkeypatch.setattr(litellm, "completion", fake_completion)
    backend = LiteLLMTurnBackend(
        model="deepseek/deepseek-chat",
        json_mode=True,
        retries=3,
    )

    events = list(backend.stream(_context()))

    finish = [event for event in events if event.type == "model.finish"]
    assert len(calls) == 2
    assert finish[0].payload == {"json": {"ok": True}}
    assert "Return only a valid JSON object" in calls[1]["messages"][-1]["content"]
