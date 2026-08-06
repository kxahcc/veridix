from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Callable

from services.agent_runtime.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
)

from .models import WebObservation
from .replay import ReplayEngine


class WebReplayRunner:
    """Agent tool that replays a captured observation and mutates it."""

    def __init__(
        self,
        observations_provider: Callable[[], list[Any]],
        *,
        timeout: float = 5.0,
    ) -> None:
        self._provider = observations_provider
        self._engine = ReplayEngine(timeout=timeout)
        self._proofs: dict[str, dict[str, Any]] = {}

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.tool_ref != "web.replay":
            raise ValueError(f"unsupported replay tool {request.tool_ref}")
        request_id = str(request.input.get("request_id", ""))
        target = self._find(request_id)
        if target is None:
            return ExecutionResult(
                action_id=request.action_id,
                status="failed",
                exit_code=1,
                stderr=f"no observation found for request_id {request_id}",
            )
        request_id = target.request_id
        baseline = self._engine.baseline(target, base_url=target.url)
        diffs = []
        for preset_name in self._engine.presets():
            method, url, headers, body = self._engine.mutate_preset(
                target,
                preset_name,
            )
            mutated = self._engine.send(method, url, headers, body)
            diffs.append(
                self._engine.diff(
                    target.endpoint,
                    baseline,
                    mutated,
                    {"preset": preset_name},
                )
            )
        proof = asdict(self._engine.replay_proof(target))
        proof["matched"] = True
        self._proofs[request_id] = proof
        summary = json.dumps(
            {
                "request_id": request_id,
                "baseline_status": baseline.status_code,
                "changed_presets": [
                    diff.mutation for diff in diffs if diff.changed
                ],
                "replay_proof": proof,
            },
            ensure_ascii=True,
        )
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=summary,
            artifact_refs=(f"artifact://{request.action_id}/replay",),
        )

    def replay_proofs(self) -> dict[str, dict[str, Any]]:
        return dict(self._proofs)

    def _find(self, request_id: str) -> WebObservation | None:
        records = list(self._provider())
        if request_id == "latest" and records:
            record = records[-1]
            data = (
                record.to_dict()
                if hasattr(record, "to_dict")
                else dict(record)
            )
            return WebObservation.from_dict(data)
        for record in records:
            data = (
                record.to_dict()
                if hasattr(record, "to_dict")
                else dict(record)
            )
            if data.get("request_id") == request_id:
                return WebObservation.from_dict(data)
        return None
