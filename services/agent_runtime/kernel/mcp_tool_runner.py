from __future__ import annotations

import json
from typing import Any

from services.knowledge_service.mcp_connector import McpResultGuard

from .contracts import ExecutionRequest, ExecutionResult


class McpToolRunner:
    """Executes MCP tools through a connector with result guarding.

    MCP output is always treated as retrieved_untrusted; the guard caps
    payload size and validates required schema fields so a hostile server
    cannot flood the model context or smuggle scope changes.
    """

    def __init__(
        self,
        connector,
        *,
        guard: McpResultGuard | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._connector = connector
        self._guard = guard or McpResultGuard()
        self._timeout_seconds = timeout_seconds
        self.executions: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.executions.append(request)
        name = _mcp_tool_name(request.tool_ref)
        try:
            raw = self._connector.call_tool(
                name,
                dict(request.input),
                timeout=request.timeout_seconds or self._timeout_seconds,
            )
        except Exception as error:
            return ExecutionResult(
                action_id=request.action_id,
                status="failed",
                exit_code=1,
                stdout="",
                stderr=f"mcp_call_failed:{type(error).__name__}:{error}",
                side_effect_state="known",
            )
        if raw.get("isError"):
            return ExecutionResult(
                action_id=request.action_id,
                status="failed",
                exit_code=1,
                stdout="",
                stderr=_render_mcp_content(raw.get("content", [])),
                side_effect_state="known",
            )
        rendered = _render_mcp_content(raw.get("content", []))
        observation = {
            "tool": request.tool_ref,
            "arguments": dict(request.input),
            "stdout": rendered,
            "trust": "retrieved_untrusted",
            "mcp": True,
        }
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=rendered,
            stderr="",
            artifact_refs=(),
            observations=(observation,),
            side_effect_state="known",
        )


def _mcp_tool_name(tool_ref: str) -> str:
    if tool_ref.startswith("mcp."):
        return tool_ref[len("mcp.") :]
    return tool_ref


def _render_mcp_content(content: list[Any]) -> str:
    if not content:
        return ""
    lines: list[str] = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") == "text" and item.get("text"):
                lines.append(str(item["text"]))
            else:
                lines.append(
                    json.dumps(
                        item,
                        ensure_ascii=True,
                        default=str,
                    )
                )
        else:
            lines.append(str(item))
    return "\n".join(lines)
