from __future__ import annotations

import json
from typing import Any

import httpx

from services.agent_runtime.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
)


class AuthzMatrixRunner:
    """Two-context authorization matrix tester over real HTTP."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self.executions: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.executions.append(request)
        endpoint = str(
            request.input.get("endpoint")
            or request.input.get("url")
            or ""
        )
        method = str(request.input.get("method", "GET")).upper()
        low_token = str(request.input.get("low_privilege_token", ""))
        high_token = str(request.input.get("high_privilege_token", ""))
        object_id = str(request.input.get("object_id", ""))
        if not endpoint:
            return ExecutionResult(
                action_id=request.action_id,
                status="failed",
                exit_code=1,
                stderr="authz tester requires endpoint",
                side_effect_state="known",
            )
        baseline_status, baseline_body = self._request(
            method,
            endpoint,
            high_token,
        )
        mutated_status, mutated_body = self._request(
            method,
            endpoint,
            low_token,
        )
        candidate = _idor_candidate(
            baseline_status,
            mutated_status,
            baseline_body,
            mutated_body,
        )
        observation: dict[str, Any] = {
            "protocol": "http",
            "endpoint": endpoint,
            "method": method,
            "object_id": object_id,
            "role": "low_privilege",
            "authz_status": "allowed" if candidate else "denied",
            "baseline_status": baseline_status,
            "mutated_status": mutated_status,
            "response_diff": (
                "changed" if mutated_body != baseline_body else "same"
            ),
        }
        if candidate:
            observation["vuln_category"] = "IDOR"
            observation["replay_proof"] = {
                "endpoint": endpoint,
                "method": method,
                "baseline_status": baseline_status,
                "mutated_status": mutated_status,
                "baseline_body_digest": _digest(baseline_body),
                "mutated_body_digest": _digest(mutated_body),
            }
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(
                {"observation_count": 1},
                ensure_ascii=True,
            ),
            observations=(observation,),
            artifact_refs=(f"artifact://authz/{request.action_id}",),
            side_effect_state="known",
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        token: str,
    ) -> tuple[int, dict]:
        headers = (
            {"Authorization": f"Bearer {token}"} if token else {}
        )
        try:
            response = httpx.request(
                method,
                endpoint,
                headers=headers,
                timeout=self._timeout,
            )
            try:
                body = response.json()
            except Exception:
                body = {"body": response.text[:500]}
            return response.status_code, body
        except httpx.HTTPError as error:
            return 0, {"error": str(error)}


def _idor_candidate(
    baseline_status: int,
    mutated_status: int,
    baseline_body: dict,
    mutated_body: dict,
) -> bool:
    return (
        baseline_status == 200
        and mutated_status == 200
        and mutated_body != baseline_body
        and "error" not in mutated_body
    )


def _digest(body: dict) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()
