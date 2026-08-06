from __future__ import annotations

import os
from typing import Any, Iterable

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_LOG", "WARNING")

from services.agent_runtime.kernel.contracts import ContextView, ModelEvent, ToolCall
from services.agent_runtime.kernel.ports import TurnBackendPort

from .openai_adapter import (
    ProviderError,
    _parse_json_payload,
    build_turn_messages,
    _provider_tool_name,
    _resolve_secret,
    _stream_response,
)


class LiteLLMTurnBackend(TurnBackendPort):
    """Multi-provider turn backend backed by LiteLLM."""

    def __init__(
        self,
        *,
        model: str,
        api_base: str | None = None,
        api_key: str | None = None,
        tool_schemas: dict[str, dict] | None = None,
        tool_descriptions: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
        max_tokens: int = 1024,
        thinking_mode: str | None = None,
        tool_choice: str | None = None,
        streaming: bool = False,
        reasoning_effort: str | None = None,
        retries: int = 5,
        litellm_provider: str = "",
        json_mode: bool = False,
    ) -> None:
        self._model = model
        self._api_base = api_base
        self._api_key = _resolve_secret(api_key)
        self._tool_schemas = tool_schemas or {}
        self._tool_descriptions = tool_descriptions or {}
        self._provider_to_internal = {
            _provider_tool_name(name): name for name in self._tool_schemas
        }
        self._timeout_seconds = timeout_seconds
        self._max_tokens = max_tokens
        self._thinking_mode = thinking_mode
        self._tool_choice = tool_choice
        self._streaming = streaming
        self._reasoning_effort = reasoning_effort
        self._retries = max(1, int(retries or 5))
        self._litellm_provider = litellm_provider
        self._json_mode = json_mode
        self.last_usage: dict[str, int] | None = None

    def stream(self, context: ContextView) -> Iterable[ModelEvent]:
        import litellm

        messages = build_turn_messages(
            context,
            json_mode=self._json_mode,
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": _provider_tool_name(name),
                    "description": self._tool_descriptions.get(
                        name,
                        f"Veridix tool {name}",
                    ),
                    "parameters": schema,
                },
            }
            for name, schema in self._tool_schemas.items()
        ]
        model = (
            self._model
            if self._api_base or not self._litellm_provider
            else f"{self._litellm_provider}/{self._model}"
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "max_tokens": self._max_tokens,
            "stream": self._streaming,
            "timeout": self._timeout_seconds,
            "num_retries": self._retries,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if self._thinking_mode:
            extra_body = kwargs.setdefault("extra_body", {})
            extra_body["thinking"] = {"type": self._thinking_mode}
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        if self._tool_choice:
            kwargs["tool_choice"] = self._tool_choice
        if self._json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            current_kwargs = kwargs
            if self._json_mode and not self._streaming:
                for attempt in range(max(1, self._retries)):
                    response = litellm.completion(**current_kwargs)
                    message = response.choices[0].message
                    content = message.content or ""
                    if _parse_json_payload(content) is not None:
                        break
                    if attempt + 1 < self._retries:
                        current_kwargs = dict(current_kwargs)
                        current_kwargs["messages"] = [
                            *current_kwargs["messages"],
                            {
                                "role": "user",
                                "content": (
                                    "Return only a valid JSON object. "
                                    "Do not include reasoning or prose."
                                ),
                            },
                        ]
            else:
                response = litellm.completion(**kwargs)
        except Exception as error:
            raise ProviderError("provider_unavailable", str(error)) from error

        if self._streaming:
            yield from _stream_response(
                response,
                self._provider_to_internal,
            )
            return

        usage = getattr(response, "usage", None)
        if usage is not None:
            self.last_usage = {
                "prompt_tokens": int(
                    getattr(usage, "prompt_tokens", 0) or 0
                ),
                "completion_tokens": int(
                    getattr(usage, "completion_tokens", 0) or 0
                ),
                "total_tokens": int(
                    getattr(usage, "total_tokens", 0) or 0
                ),
            }
            yield ModelEvent(type="model.usage", payload=dict(self.last_usage))

        message = response.choices[0].message
        reasoning = getattr(message, "reasoning_content", None)
        if message.tool_calls:
            for call in message.tool_calls:
                try:
                    import json

                    parsed = json.loads(call.function.arguments or "{}")
                except Exception:
                    parsed = {}
                internal_name = self._provider_to_internal.get(
                    call.function.name,
                    call.function.name,
                )
                yield ModelEvent(
                    type="model.tool_call",
                    tool_call=ToolCall(
                        id=call.id or "call_unknown",
                        name=internal_name,
                        arguments=parsed,
                    ),
                    reasoning_content=reasoning,
                )
        if message.content:
            payload = None
            if self._json_mode:
                payload = {
                    "json": _parse_json_payload(message.content)
                }
            yield ModelEvent(
                type="model.finish",
                text=message.content,
                reasoning_content=reasoning,
                payload=payload,
            )
