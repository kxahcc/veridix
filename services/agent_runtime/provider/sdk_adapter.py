from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import Future
from typing import Any, Iterable

from services.agent_runtime.kernel.contracts import ContextView, ModelEvent, ToolCall
from services.agent_runtime.kernel.ports import TurnBackendPort


_LOOP_LOCK = threading.Lock()
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None


def _get_event_loop() -> asyncio.AbstractEventLoop:
    global _LOOP, _LOOP_THREAD
    with _LOOP_LOCK:
        if _LOOP is None or _LOOP.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever,
                daemon=True,
                name="sdk-agent-loop",
            )
            thread.start()
            _LOOP = loop
            _LOOP_THREAD = thread
        return _LOOP


def _run_coro(coro) -> Any:
    loop = _get_event_loop()
    future: Future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


class SdkTurnBackend(TurnBackendPort):
    """OpenAI Agents SDK turn adapter; Veridix executes tools via ToolBroker."""

    def __init__(
        self,
        model,
        *,
        tool_schemas: dict[str, dict] | None = None,
    ) -> None:
        self._model = model
        self._tool_schemas = tool_schemas or {
            "shell.probe": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
                "additionalProperties": False,
            }
        }

    def stream(self, context: ContextView) -> Iterable[ModelEvent]:
        try:
            from agents import Agent, Runner, set_tracing_disabled
            from agents.items import ToolCallItem
            from agents.tool import FunctionTool
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "openai-agents is not installed; native backend remains available"
            ) from exc

        set_tracing_disabled(True)

        async def noop_tool(ctx, arguments: str) -> str:
            return arguments

        stubs = [
            FunctionTool(
                name=name,
                description=f"Veridix projection stub for {name}",
                params_json_schema=schema,
                on_invoke_tool=noop_tool,
            )
            for name, schema in self._tool_schemas.items()
        ]
        agent = Agent(
            name="veridix-agent-worker",
            model=self._model,
            instructions=context.mission,
            tools=stubs,
            tool_use_behavior="stop_on_first_tool",
            output_type=str,
        )
        prompt = context.mission
        if context.observations:
            prompt += "\nObservations:\n" + json.dumps(
                list(context.observations),
                ensure_ascii=True,
            )
        result = _run_coro(Runner.run(agent, prompt, max_turns=1))
        tool_calls = [
            item for item in result.new_items if isinstance(item, ToolCallItem)
        ]
        if tool_calls:
            raw = tool_calls[0].raw_item
            name = getattr(raw, "name", None) or raw.get("name")
            call_id = getattr(raw, "call_id", None) or raw.get("call_id") or "call_unknown"
            arguments = getattr(raw, "arguments", None) or raw.get("arguments")
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            yield ModelEvent(
                type="model.tool_call",
                tool_call=ToolCall(id=str(call_id), name=str(name), arguments=parsed),
            )
            return
        final_text = result.final_output
        if final_text is not None:
            yield ModelEvent(type="model.finish", text=str(final_text))
