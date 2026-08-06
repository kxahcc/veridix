from __future__ import annotations

import json
from typing import Any

from services.agent_runtime.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
)


class ConnectorToolRunner:
    """Exposes an external scanner connector as a Veridix agent tool."""

    def __init__(
        self,
        connector: Any,
        connector_kind: str,
    ) -> None:
        self._connector = connector
        self._kind = connector_kind
        self._last_observations: list[dict[str, Any]] = []

    def observations(self) -> list[dict[str, Any]]:
        return list(self._last_observations)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        target = str(
            request.input.get("target")
            or request.input.get("url")
            or ""
        )
        if target:
            if self._kind == "zap":
                if str(request.input.get("mode", "active")).lower() == "spider":
                    self._connector.spider(target)
                else:
                    self._connector.active_scan(target)
            elif self._kind == "caido":
                scan_id = self._connector.create_scan(target)
                self._connector.scan_status(scan_id)
            elif self._kind == "burp":
                self._connector.start_scan(target)
            else:
                raise ValueError(f"unknown connector kind {self._kind}")
        observations = tuple(self._connector.export_observations())
        self._last_observations = list(observations)
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(
                list(observations),
                ensure_ascii=True,
                default=str,
            ),
            artifact_refs=(f"artifact://{self._kind}/scan",),
            observations=observations,
        )


class UnavailableToolRunner:
    """Fail-closed runner for connector tools without configured endpoint."""

    def __init__(self, tool_ref: str) -> None:
        self._tool_ref = tool_ref

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            action_id=request.action_id,
            status="failed",
            exit_code=1,
            stderr=f"{self._tool_ref} connector is not configured",
        )
