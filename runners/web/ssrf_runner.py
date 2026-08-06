from __future__ import annotations

import json
from typing import Any

import httpx

from services.agent_runtime.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
)


class SSRFTesterRunner:
    """Sends a blind SSRF fetch to a callback URL."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self.executions: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.executions.append(request)
        callback_url = str(
            request.input.get("callback_url")
            or request.input.get("url")
            or ""
        )
        if not callback_url:
            return ExecutionResult(
                action_id=request.action_id,
                status="failed",
                exit_code=1,
                stderr="ssrf tester requires callback_url",
                side_effect_state="known",
            )
        try:
            response = httpx.get(
                callback_url,
                timeout=self._timeout,
            )
            status = response.status_code
            body = response.text[:500]
        except httpx.HTTPError as error:
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                exit_code=0,
                stdout=f"fetch_error:{error}",
                observations=(
                    {
                        "kind": "ssrf_fetch",
                        "callback_url": callback_url,
                        "status": 0,
                        "error": str(error),
                    },
                ),
                artifact_refs=(f"artifact://ssrf/{request.action_id}",),
                side_effect_state="known",
            )
        observation: dict[str, Any] = {
            "kind": "ssrf_fetch",
            "callback_url": callback_url,
            "status": status,
            "body": body,
        }
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(
                {"status": status},
                ensure_ascii=True,
            ),
            observations=(observation,),
            artifact_refs=(f"artifact://ssrf/{request.action_id}",),
            side_effect_state="known",
        )
