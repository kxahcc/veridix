from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolPreview:
    name: str
    description: str
    input_schema: dict[str, Any]
    source: str = "mcp"
    trust: str = "retrieved_untrusted"


class LocalMcpConnector:
    """Lazy stdio MCP discovery; tools are never called during projection."""

    def __init__(self, command: list[str], *, cwd: str | None = None) -> None:
        self._command = command
        self._cwd = cwd

    def list_tools(self, timeout: float = 10.0) -> list[ToolPreview]:
        return asyncio.run(self._list_tools_async(timeout))

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        return asyncio.run(
            self._call_tool_async(name, arguments, timeout)
        )

    async def _list_tools_async(self, timeout: float) -> list[ToolPreview]:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=self._command[0],
            args=self._command[1:],
            cwd=self._cwd,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=timeout)
                tools = await asyncio.wait_for(session.list_tools(), timeout=timeout)
                return [
                    ToolPreview(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=dict(tool.inputSchema or {}),
                    )
                    for tool in tools.tools
                ]

    async def _call_tool_async(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=self._command[0],
            args=self._command[1:],
            cwd=self._cwd,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=timeout)
                result = await asyncio.wait_for(
                    session.call_tool(name, arguments),
                    timeout=timeout,
                )
                return {
                    "content": [
                        item.model_dump()
                        if hasattr(item, "model_dump")
                        else dict(item)
                        for item in result.content
                    ],
                    "isError": bool(getattr(result, "isError", False)),
                }


class ContainerMcpConnector(LocalMcpConnector):
    """Runs an MCP server inside a container via `docker exec` stdio."""

    def __init__(
        self,
        command: list[str],
        *,
        image: str,
        container_name: str,
    ) -> None:
        self._image = image
        self._container_name = container_name
        self._inner_command = command
        super().__init__(self.build_command(), cwd=None)

    def build_command(self) -> list[str]:
        return [
            "docker",
            "exec",
            "-i",
            self._container_name,
            *self._inner_command,
        ]

    def ensure_container(self, timeout: float = 30.0) -> str:
        import docker
        from docker.errors import NotFound

        client = docker.from_env()
        try:
            container = client.containers.get(self._container_name)
        except NotFound:
            container = client.containers.run(
                self._image,
                detach=True,
                command=["sleep", "infinity"],
                name=self._container_name,
                network_mode="none",
            )
        return container.id

    def list_tools(self, timeout: float = 10.0) -> list[ToolPreview]:
        self.ensure_container(timeout=timeout)
        return super().list_tools(timeout)


def project_tools(
    previews: list[ToolPreview],
    *,
    node_type: str,
    allowed_tools: tuple[str, ...],
) -> tuple[tuple[ToolPreview, ...], tuple[dict[str, str], ...]]:
    included: list[ToolPreview] = []
    omitted: list[dict[str, str]] = []
    for preview in previews:
        if preview.name not in allowed_tools:
            omitted.append(
                {"name": preview.name, "reason": "tool_not_in_projection"}
            )
            continue
        if node_type not in ("web_discovery", "verifier", "host"):
            omitted.append(
                {"name": preview.name, "reason": "node_type_not_supported"}
            )
            continue
        included.append(preview)
    return tuple(included), tuple(omitted)


class McpResultGuard:
    def __init__(self, *, max_bytes: int = 512 * 1024) -> None:
        self._max_bytes = max_bytes

    def sanitize(self, value: Any) -> dict[str, Any]:
        encoded = json.dumps(value, ensure_ascii=True, default=str)
        truncated = len(encoded.encode("utf-8")) > self._max_bytes
        if truncated:
            encoded = encoded[: self._max_bytes]
            value = {"truncated": True, "preview": encoded}
        return {
            "value": value,
            "trust": "retrieved_untrusted",
            "truncated": truncated,
        }

    def validate_schema(self, value: dict[str, Any], schema: dict[str, Any]) -> bool:
        required = schema.get("required", [])
        return all(key in value for key in required)
