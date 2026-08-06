from __future__ import annotations

import json
import os
import time
from typing import Any, Iterable

import httpx

from services.agent_runtime.kernel.contracts import ContextView, ModelEvent, ToolCall
from services.agent_runtime.kernel.ports import TurnBackendPort


DEFAULT_TOOL_SCHEMAS: dict[str, dict] = {
    "shell.probe": {
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
        "additionalProperties": False,
    },
    "run.finish": {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    },
    "skill.read": {
        "type": "object",
        "properties": {
            "skill_ref": {
                "type": "string",
                "description": "Included skill package name",
            },
            "path": {
                "type": "string",
                "description": (
                    "Package-relative path such as "
                    "references/checklist.md"
                ),
            },
        },
        "required": ["skill_ref", "path"],
        "additionalProperties": False,
    },
    "memory.recall": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Semantic or lexical search query over project memory",
            },
            "subject": {"type": "string"},
            "predicate": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "include_stale": {
                "type": "boolean",
                "description": "Include stale facts in the result",
            },
        },
        "additionalProperties": False,
    },
    "memory.record": {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Fact subject, usually an endpoint, host or asset",
            },
            "predicate": {
                "type": "string",
                "description": "Fact predicate such as observed:nikto.scan",
            },
            "value": {"type": "string", "description": "Observed fact value"},
            "target": {"type": "string"},
            "source_refs": {
                "type": "array",
                "items": {"type": "string"},
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "expires_in_seconds": {
                "type": "integer",
                "description": "Optional TTL for volatile facts",
            },
            "metadata": {"type": "object"},
        },
        "required": ["subject", "predicate", "value"],
        "additionalProperties": False,
    },
    "memory.status": {
        "type": "object",
        "properties": {
            "summary_limit": {"type": "integer", "minimum": 0, "maximum": 20}
        },
        "additionalProperties": False,
    },
    "browser.open": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    },
    "proxy.list": {
        "type": "object",
        "properties": {"endpoint": {"type": "string"}},
        "required": ["endpoint"],
        "additionalProperties": False,
    },
    "web.replay": {
        "type": "object",
        "properties": {"request_id": {"type": "string"}},
        "required": ["request_id"],
        "additionalProperties": False,
    },
}


class ProviderError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retry_after_seconds = retry_after_seconds


def build_turn_messages(
    context: ContextView,
    *,
    json_mode: bool = False,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a security testing agent. Work efficiently: "
                "gather evidence, then call run.finish with a summary. "
                "Never repeat an identical tool call without new evidence; "
                "if an action produced no new evidence, choose a different "
                "action or finish. Use project memory tools when they are "
                "available: call memory.recall before acting when prior "
                "knowledge may exist, call memory.record after high-value "
                "observations such as endpoints, versions, credentials, "
                "scanner results or verified findings, and call memory.status "
                "when you need a snapshot of what is already known. Tool "
                "names use underscores in the function list for dotted refs: "
                "web.nikto.scan is web_nikto_scan, memory.recall is "
                "memory_recall, memory.record is memory_record."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Target in scope: {context.target_ref}\n"
                f"Mission: {context.mission}"
            ),
        },
    ]
    if json_mode:
        messages.append(
            {
                "role": "system",
                "content": (
                    "You must respond with a single valid JSON object. "
                    "The JSON object must be the entire response."
                ),
            }
        )
    blocks = context.context_blocks
    if blocks is not None and not blocks.empty:
        messages.append(
            {
                "role": "system",
                "content": _render_context_blocks(blocks),
            }
        )
    if context.observations:
        assistant_calls = []
        for index, observation in enumerate(context.observations):
            call_id = str(
                observation.get("tool_call_id") or f"call_{index}"
            )
            assistant_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": _provider_tool_name(
                            str(observation.get("tool", ""))
                        ),
                        "arguments": json.dumps(
                            observation.get("arguments") or {},
                            ensure_ascii=True,
                        ),
                    },
                }
            )
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": assistant_calls,
        }
        for observation in context.observations:
            if observation.get("reasoning_content"):
                assistant_message["reasoning_content"] = observation[
                    "reasoning_content"
                ]
                break
        messages.append(assistant_message)
        for index, observation in enumerate(context.observations):
            call_id = str(
                observation.get("tool_call_id") or f"call_{index}"
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(observation.get("stdout", "")),
                }
            )
    return messages


class OpenAICompatibleTurnBackend(TurnBackendPort):
    """OpenAI-compatible chat completions adapter with error classification."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
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
        json_mode: bool = False,
    ) -> None:
        from openai import OpenAI

        self._model = model
        self._thinking_mode = thinking_mode
        self._reasoning_effort = reasoning_effort
        self._retries = max(1, int(retries or 5))
        self._json_mode = json_mode
        self._tool_choice = tool_choice
        self._streaming = streaming
        self._tool_schemas = (
            dict(DEFAULT_TOOL_SCHEMAS)
            if tool_schemas is None
            else dict(tool_schemas)
        )
        self._tool_descriptions = tool_descriptions or {}
        self._provider_to_internal = {
            _provider_tool_name(name): name for name in self._tool_schemas
        }
        resolved_key = _resolve_secret(api_key)
        self._client = OpenAI(
            base_url=base_url,
            api_key=resolved_key or "probe-only",
            timeout=timeout_seconds,
            http_client=httpx.Client(
                trust_env=False,
                timeout=timeout_seconds,
            ),
        )
        self._max_tokens = max_tokens
        self.last_usage: dict[str, int] | None = None

    def stream(self, context: ContextView) -> Iterable[ModelEvent]:
        messages = self._build_messages(
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
        try:
            request_kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "tools": tools,
                "max_tokens": self._max_tokens,
                "stream": False,
            }
            if self._thinking_mode or self._reasoning_effort:
                extra_body = request_kwargs.setdefault("extra_body", {})
                if self._thinking_mode:
                    extra_body["thinking"] = {"type": self._thinking_mode}
                if self._reasoning_effort:
                    extra_body["reasoning_effort"] = self._reasoning_effort
            if self._tool_choice:
                request_kwargs["tool_choice"] = self._tool_choice
            if self._json_mode:
                request_kwargs["response_format"] = {"type": "json_object"}
            if self._streaming:
                request_kwargs["stream"] = True
            current_kwargs = request_kwargs
            if self._json_mode and not self._streaming:
                for attempt in range(max(1, self._retries)):
                    response = self._create_with_retry(current_kwargs)
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
                response = self._create_with_retry(request_kwargs)
        except Exception as error:
            if isinstance(error, ProviderError):
                raise error
            raise self._classify(error) from error

        if self._streaming:
            yield from _stream_response(response, self._provider_to_internal)
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
            yield ModelEvent(
                type="model.usage",
                payload=dict(self.last_usage),
            )

        message = response.choices[0].message
        reasoning = getattr(message, "reasoning_content", None)
        if message.tool_calls:
            for call in message.tool_calls:
                arguments = call.function.arguments or "{}"
                try:
                    parsed = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed = {}
                internal_name = self._provider_to_internal.get(
                    call.function.name, call.function.name
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

    def _create_with_retry(self, request_kwargs: dict[str, Any]) -> Any:
        attempts = self._retries
        last_error: ProviderError | None = None
        for attempt in range(attempts):
            try:
                return self._client.chat.completions.create(**request_kwargs)
            except Exception as error:
                if isinstance(error, ProviderError):
                    raise error
                classified = self._classify(error)
                last_error = classified
                if (
                    classified.category
                    in (
                        "provider_timeout",
                        "provider_rate_limit",
                        "provider_unavailable",
                    )
                    and attempt + 1 < attempts
                ):
                    delay = min(
                        3.0,
                        (
                            classified.retry_after_seconds
                            if classified.retry_after_seconds is not None
                            else 0.5 * (attempt + 1)
                        ),
                    )
                    time.sleep(delay)
                    continue
                raise classified from error
        raise last_error  # type: ignore[misc]

    def _build_messages(
        self,
        context: ContextView,
        *,
        json_mode: bool = False,
    ) -> list[dict[str, Any]]:
        return build_turn_messages(context, json_mode=json_mode)

    def _classify(self, error: Exception) -> ProviderError:
        name = type(error).__name__
        if "Timeout" in name:
            return ProviderError("provider_timeout", str(error))
        if "RateLimit" in name:
            retry_after = _parse_retry_after(
                getattr(getattr(error, "response", None), "headers", None)
            )
            return ProviderError(
                "provider_rate_limit",
                str(error),
                retry_after_seconds=retry_after,
            )
        if getattr(error, "status_code", None) == 429:
            retry_after = _parse_retry_after(
                getattr(getattr(error, "response", None), "headers", None)
            )
            return ProviderError(
                "provider_rate_limit",
                str(error),
                retry_after_seconds=retry_after,
            )
        if "Authentication" in name:
            return ProviderError("provider_auth", str(error))
        if "APIStatusError" in name:
            status = getattr(error, "status_code", None)
            if status in (401, 403):
                return ProviderError("provider_auth", str(error))
            if status == 429:
                retry_after = _parse_retry_after(
                    getattr(getattr(error, "response", None), "headers", None)
                )
                return ProviderError(
                    "provider_rate_limit",
                    str(error),
                    retry_after_seconds=retry_after,
                )
            if status is not None and status >= 500:
                return ProviderError("provider_unavailable", str(error))
        return ProviderError("provider_unavailable", str(error))


def _render_context_blocks(blocks) -> str:
    sections: list[str] = []
    if blocks.knowledge:
        sections.append(
            "## Projected knowledge\n"
            + "\n".join(f"- {item}" for item in blocks.knowledge)
        )
    if blocks.memory:
        sections.append(
            "## Project memory\n"
            + "\n".join(f"- {item}" for item in blocks.memory)
        )
    if blocks.skills:
        sections.append(
            "## Available skills\n"
            + "\n".join(f"- {item}" for item in blocks.skills)
        )
    if blocks.mcp:
        sections.append(
            "## MCP tool previews\n"
            + "\n".join(f"- {item}" for item in blocks.mcp)
        )
    if blocks.summaries:
        sections.append(
            "## Conversation summary\n"
            + "\n".join(f"- {item}" for item in blocks.summaries)
        )
    if blocks.digest:
        sections.append(f"Context digest: {blocks.digest}")
    return "\n\n".join(sections)


def _resolve_secret(ref: str | None) -> str | None:
    if not ref:
        return None
    scheme, _, name = ref.partition(":")
    if scheme == "env" and name:
        return os.environ.get(name)
    return None


def _provider_tool_name(name: str) -> str:
    """Map Veridix dotted tool refs to provider-safe identifiers."""
    return name.replace(".", "_")


def _parse_retry_after(headers) -> float | None:
    if headers is None:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_json_payload(text: str) -> Any | None:
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _stream_response(response, provider_to_internal: dict[str, str]):
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    reasoning = None
    for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if getattr(delta, "reasoning_content", None):
            reasoning = delta.reasoning_content
        if delta.content:
            content_parts.append(delta.content)
            yield ModelEvent(type="model.delta", text=delta.content)
        for call in delta.tool_calls or []:
            entry = tool_calls.setdefault(
                call.index,
                {"id": None, "name": "", "arguments": ""},
            )
            if call.id:
                entry["id"] = call.id
            if call.function and call.function.name:
                entry["name"] += call.function.name
            if call.function and call.function.arguments:
                entry["arguments"] += call.function.arguments
    if tool_calls:
        for entry in tool_calls.values():
            try:
                arguments = json.loads(entry["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            internal_name = provider_to_internal.get(entry["name"], entry["name"])
            yield ModelEvent(
                type="model.tool_call",
                tool_call=ToolCall(
                    id=entry["id"] or "call_unknown",
                    name=internal_name,
                    arguments=arguments,
                ),
                reasoning_content=reasoning,
            )
        return
    text = "".join(content_parts)
    if text:
        yield ModelEvent(type="model.finish", text=text, reasoning_content=reasoning)
