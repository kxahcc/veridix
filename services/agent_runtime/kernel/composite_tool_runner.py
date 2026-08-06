from __future__ import annotations

from typing import Any

from .contracts import ExecutionRequest, ExecutionResult


class CompositeToolRunner:
    """Routes tool calls to dedicated runners by tool_ref."""

    def __init__(
        self,
        runners: dict[str, Any],
        *,
        default: Any = None,
    ) -> None:
        self._runners = dict(runners)
        self._default = default

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        runner = self._runners.get(request.tool_ref, self._default)
        if runner is None:
            raise ValueError(f"no runner for tool {request.tool_ref}")
        return runner.execute(request)

    def observations(self):
        records = []
        for runner in self._runners.values():
            collector = getattr(runner, "observations", None)
            if callable(collector):
                records.extend(collector())
        return records

    def replay_proofs(self):
        proofs: dict[str, Any] = {}
        for runner in self._runners.values():
            provider = getattr(runner, "replay_proofs", None)
            if callable(provider):
                proofs.update(provider())
            elif isinstance(provider, dict):
                proofs.update(provider)
        return proofs
