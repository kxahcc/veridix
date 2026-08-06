from __future__ import annotations

import sys
from pathlib import Path

from services.knowledge_service.mcp_connector import (
    LocalMcpConnector,
    McpResultGuard,
    project_tools,
)

ROOT = Path(__file__).resolve().parents[2]


def test_mcp_connector_lists_tools_lazily() -> None:
    connector = LocalMcpConnector(
        [sys.executable, "-m", "services.knowledge_service.mock_mcp_server"],
        cwd=str(ROOT),
    )

    tools = connector.list_tools(timeout=15)

    assert any(tool.name == "web_lookup" for tool in tools)
    assert all(tool.trust == "retrieved_untrusted" for tool in tools)


def test_mcp_connector_calls_tool_over_stdio() -> None:
    connector = LocalMcpConnector(
        [sys.executable, "-m", "services.knowledge_service.mock_mcp_server"],
        cwd=str(ROOT),
    )

    result = connector.call_tool(
        "web_lookup",
        {"target": "https://lab.example.test", "path": "/admin"},
        timeout=15,
    )

    rendered = "".join(
        str(item.get("text", ""))
        for item in result["content"]
        if isinstance(item, dict)
    )
    assert result["isError"] is False
    assert "https://lab.example.test/admin" in rendered


def test_tool_projection_omits_unallowed_tools() -> None:
    connector = LocalMcpConnector(
        [sys.executable, "-m", "services.knowledge_service.mock_mcp_server"],
        cwd=str(ROOT),
    )
    tools = connector.list_tools(timeout=15)

    included, omitted = project_tools(
        tools,
        node_type="web_discovery",
        allowed_tools=("other.tool",),
    )

    assert included == ()
    assert omitted[0]["reason"] == "tool_not_in_projection"


def test_mcp_result_guard_marks_untrusted_and_validates() -> None:
    guard = McpResultGuard(max_bytes=8)

    sanitized = guard.sanitize({"endpoint": "/admin", "status": 200})
    assert sanitized["trust"] == "retrieved_untrusted"
    assert sanitized["truncated"] is True

    schema = {"type": "object", "required": ["endpoint"]}
    assert guard.validate_schema({"endpoint": "/admin"}, schema) is True
    assert guard.validate_schema({"status": 200}, schema) is False
