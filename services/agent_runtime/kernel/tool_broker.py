from __future__ import annotations

import json
from typing import Callable

from .contracts import (
    AgentRunSpec,
    ExecutionOutcome,
    ExecutionResult,
    ExecutionRequest,
    PolicyDecision,
    ToolCall,
)
from urllib.parse import urlparse
from .fake_runner import FakeRunner
from .ports import ToolBrokerPort


RISK_ORDER = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}


def _target_in_scope(target: str, allowed_targets: tuple[str, ...]) -> bool:
    if target in allowed_targets:
        return True
    hostname = urlparse(target).hostname or target.split(":")[0]
    for allowed in allowed_targets:
        if urlparse(allowed).hostname == hostname:
            return True
    return False


class ToolBroker(ToolBrokerPort):
    def __init__(
        self,
        runner: FakeRunner,
        *,
        risk_resolver: Callable[[str], str] | None = None,
        max_risk_level: str = "L4",
    ) -> None:
        self._runner = runner
        self._risk_resolver = risk_resolver
        self._max_risk_level = max_risk_level
        self._completed: dict[str, ExecutionOutcome] = {}
        self._unknown: dict[str, ExecutionOutcome] = {}
        self._cache: dict[tuple[str, str], str] = {}

    def authorize(self, call: ToolCall, spec: AgentRunSpec) -> PolicyDecision:
        if call.name not in spec.allowed_tools:
            return PolicyDecision(
                allowed=False,
                risk_level="L1",
                rule="tool_not_in_projection",
                explanation=f"tool {call.name} is not allowed for this run",
            )
        target = call.arguments.get("target")
        if target and not _target_in_scope(target, spec.allowed_targets):
            return PolicyDecision(
                allowed=False,
                risk_level="L1",
                rule="target_out_of_scope",
                explanation=f"target {target} is outside allowed_targets",
            )
        if self._risk_resolver is not None:
            risk = self._risk_resolver(call.name)
            if RISK_ORDER.get(risk, 1) > RISK_ORDER.get(
                self._max_risk_level,
                4,
            ):
                return PolicyDecision(
                    allowed=False,
                    risk_level=risk,
                    rule="risk_level_denied",
                    explanation=(
                        f"tool {call.name} risk {risk} exceeds "
                        f"max_tool_risk {self._max_risk_level}"
                    ),
                )
        return PolicyDecision(allowed=True, risk_level="L1", rule="allowlist")

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        if request.idempotency_key in self._completed:
            return ExecutionOutcome(
                result=self._completed[request.idempotency_key].result,
                replayed=True,
            )
        if request.idempotency_key in self._unknown:
            return ExecutionOutcome(
                result=self._unknown[request.idempotency_key].result,
                replayed=True,
            )
        cache_key = (
            request.tool_ref,
            json.dumps(request.input, sort_keys=True, ensure_ascii=True),
        )
        if cache_key in self._cache:
            return ExecutionOutcome(
                result=self._completed[self._cache[cache_key]].result,
                replayed=True,
            )
        result = self._runner.execute(request)
        outcome = ExecutionOutcome(result=result, replayed=False)
        if result.side_effect_state == "unknown":
            self._unknown[request.idempotency_key] = outcome
        else:
            self._completed[request.idempotency_key] = outcome
            self._cache[cache_key] = request.idempotency_key
        return outcome

    def snapshot_keys(self) -> dict[str, list]:
        return {
            "completed": list(self._completed),
            "unknown": list(self._unknown),
            "cache": [
                [*list(key), idempotency_key]
                for key, idempotency_key in self._cache.items()
            ],
        }

    def snapshot_outcomes(self) -> dict[str, dict]:
        def serialize(outcome: ExecutionOutcome) -> dict:
            result = outcome.result
            return {
                "replayed": outcome.replayed,
                "result": {
                    "action_id": result.action_id,
                    "status": result.status,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "artifact_refs": list(result.artifact_refs),
                    "side_effect_state": result.side_effect_state,
                    "observations": list(result.observations),
                },
            }

        return {
            "completed": {
                key: serialize(outcome)
                for key, outcome in self._completed.items()
            },
            "unknown": {
                key: serialize(outcome)
                for key, outcome in self._unknown.items()
            },
        }

    def restore_outcomes(self, payload: dict[str, dict] | None) -> None:
        self._completed = {}
        self._unknown = {}

        def deserialize(entry: dict) -> ExecutionOutcome:
            data = entry["result"]
            return ExecutionOutcome(
                result=ExecutionResult(
                    action_id=str(data["action_id"]),
                    status=str(data["status"]),
                    exit_code=data.get("exit_code"),
                    stdout=str(data.get("stdout", "")),
                    stderr=str(data.get("stderr", "")),
                    artifact_refs=tuple(data.get("artifact_refs", [])),
                    side_effect_state=str(
                        data.get("side_effect_state", "known")
                    ),
                    observations=tuple(data.get("observations", [])),
                ),
                replayed=bool(entry.get("replayed", False)),
            )

        for key, entry in (payload or {}).get("completed", {}).items():
            self._completed[str(key)] = deserialize(entry)
        for key, entry in (payload or {}).get("unknown", {}).items():
            self._unknown[str(key)] = deserialize(entry)

    def restore_keys(self, keys: dict[str, list]) -> None:
        self._completed = {key: self._completed[key] for key in keys.get("completed", [])}
        self._unknown = {key: self._unknown[key] for key in keys.get("unknown", [])}
        self._cache = {}
        for entry in keys.get("cache", []):
            tuple_key = tuple(entry[:-1])
            idempotency_key = entry[-1]
            if idempotency_key in self._completed:
                self._cache[tuple_key] = idempotency_key
