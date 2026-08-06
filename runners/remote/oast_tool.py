from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.agent_runtime.kernel.contracts import ExecutionRequest, ExecutionResult

from .oast import OastStore


class OastToolRunner:
    """Native agent tools for one-time OAST tokens and callback checks."""

    def __init__(
        self,
        *,
        store: OastStore | None = None,
        db: str | Path = ":memory:",
        base_url: str = "http://127.0.0.1:8791",
    ) -> None:
        self._store = store or OastStore(db)
        self._base_url = base_url.rstrip("/")

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.tool_ref == "oast.create":
            return self._create(request)
        if request.tool_ref == "oast.check":
            return self._check(request)
        return ExecutionResult(
            action_id=request.action_id,
            status="denied",
            stderr=f"unknown oast tool {request.tool_ref}",
        )

    def _create(self, request: ExecutionRequest) -> ExecutionResult:
        purpose = str(request.input.get("purpose") or "")
        token = self._store.issue_token(
            source="agent",
            purpose=purpose,
        )
        callback_url = f"{self._base_url}/callback/{token.token}"
        payload = {
            "token": token.token,
            "callback_url": callback_url,
            "expires_at": token.expires_at,
            "purpose": purpose,
        }
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(payload, ensure_ascii=True),
            artifact_refs=(f"artifact://{request.action_id}/oast",),
            observations=(
                {
                    "kind": "oast_token",
                    "token": token.token,
                    "callback_url": callback_url,
                    "purpose": purpose,
                    "expires_at": token.expires_at,
                },
            ),
        )

    def _check(self, request: ExecutionRequest) -> ExecutionResult:
        token = str(request.input.get("token") or "")
        records = self._store.find(token)
        observations: list[dict[str, Any]] = [
            {
                "kind": "oast_callback",
                "token": record.token,
                "callback_id": record.callback_id,
                "source": record.source,
                "observed_at": record.observed_at,
                "payload": record.payload,
            }
            for record in records
        ]
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(
                {
                    "token": token,
                    "callbacks": observations,
                },
                ensure_ascii=True,
            ),
            artifact_refs=(f"artifact://{request.action_id}/oast",),
            observations=tuple(observations),
        )

    def close(self) -> None:
        self._store.close()
