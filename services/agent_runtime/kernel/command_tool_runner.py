from __future__ import annotations

from typing import Any, Callable

from .contracts import ExecutionRequest, ExecutionResult


def build_command(tool_ref: str, arguments: dict[str, Any]) -> list[str]:
    target = str(arguments.get("target") or arguments.get("url") or "")
    if tool_ref == "nmap.scan":
        ports = str(arguments.get("ports") or "1-10000")
        return ["nmap", "-sV", "-p", ports, target]
    if tool_ref == "nuclei.scan":
        return ["nuclei-sandbox", "-u", target, "-jsonl"]
    if tool_ref == "metasploit.console":
        module = str(arguments.get("module") or "")
        command = f"use {module}; run" if module else "help"
        return ["msfconsole", "-q", "-x", command]
    if tool_ref == "shell.probe":
        return ["echo", target]
    raise ValueError(f"unsupported command tool {tool_ref}")


class CommandToolRunner:
    """Runs a command-style security tool through an inner sandbox runner."""

    def __init__(
        self,
        inner: Any,
        *,
        builder: Callable[[str, dict[str, Any]], list[str]] = build_command,
    ) -> None:
        self._inner = inner
        self._builder = builder

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        command = self._builder(request.tool_ref, request.input)
        inner_request = ExecutionRequest(
            action_id=request.action_id,
            run_id=request.run_id,
            tool_ref="sandbox.exec",
            input={"command": command},
            idempotency_key=request.idempotency_key,
            timeout_seconds=request.timeout_seconds,
        )
        return self._inner.execute(inner_request)
