from __future__ import annotations

from dataclasses import dataclass

from .mcp_connector import McpResultGuard, ToolPreview, project_tools


@dataclass(frozen=True)
class ConformanceCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ConformanceReport:
    checks: tuple[ConformanceCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


class McpConformanceHarness:
    def __init__(self, *, max_bytes: int = 512 * 1024) -> None:
        self._max_bytes = max_bytes
        self._guard = McpResultGuard(max_bytes=max_bytes)

    def run(
        self,
        connector,
        *,
        node_type: str,
        allowed_tools: tuple[str, ...],
    ) -> ConformanceReport:
        tools = connector.list_tools()
        checks = [
            self._check_lazy_discovery(tools),
            self._check_minimal_projection(tools, node_type, allowed_tools),
            self._check_trust_untrusted(tools),
            self._check_schema_preview(tools),
            self._check_result_guard(),
        ]
        return ConformanceReport(checks=tuple(checks))

    def _check_lazy_discovery(self, tools: list[ToolPreview]) -> ConformanceCheck:
        return ConformanceCheck(
            name="lazy_discovery",
            passed=len(tools) > 0,
            detail=f"discovered {len(tools)} tools without invocation",
        )

    def _check_minimal_projection(
        self,
        tools: list[ToolPreview],
        node_type: str,
        allowed_tools: tuple[str, ...],
    ) -> ConformanceCheck:
        included, omitted = project_tools(
            tools,
            node_type=node_type,
            allowed_tools=allowed_tools,
        )
        names = {tool.name for tool in included}
        passed = bool(included) and all(name in allowed_tools for name in names)
        return ConformanceCheck(
            name="minimal_projection",
            passed=passed,
            detail=(
                f"included={sorted(names)} omitted={len(omitted)}"
            ),
        )

    def _check_trust_untrusted(self, tools: list[ToolPreview]) -> ConformanceCheck:
        passed = all(tool.trust == "retrieved_untrusted" for tool in tools)
        return ConformanceCheck(
            name="trust_untrusted",
            passed=passed,
            detail="all MCP tools marked retrieved_untrusted",
        )

    def _check_schema_preview(self, tools: list[ToolPreview]) -> ConformanceCheck:
        passed = all(tool.input_schema.get("type") == "object" for tool in tools)
        return ConformanceCheck(
            name="schema_preview",
            passed=passed,
            detail="input schemas previewed before projection",
        )

    def _check_result_guard(self) -> ConformanceCheck:
        sanitized = self._guard.sanitize({"large": "x" * (self._max_bytes + 1)})
        passed = sanitized["truncated"] is True and sanitized["trust"] == "retrieved_untrusted"
        return ConformanceCheck(
            name="result_guard",
            passed=passed,
            detail="oversized results truncated and marked untrusted",
        )
