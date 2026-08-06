from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from services.agent_runtime.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
)


class GraphQLTesterRunner:
    """Baseline + mutation GraphQL tester producing protocol observations."""

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
        query = str(request.input.get("query", ""))
        operation = str(request.input.get("operation", ""))
        variables = dict(request.input.get("variables") or {})
        headers = dict(request.input.get("headers") or {})
        if not endpoint:
            return ExecutionResult(
                action_id=request.action_id,
                status="failed",
                exit_code=1,
                stderr="graphql tester requires endpoint",
                side_effect_state="known",
            )
        baseline_status, baseline_body = self._post(
            endpoint,
            query,
            operation,
            variables,
            headers,
        )
        observations: list[dict[str, Any]] = [
            {
                "protocol": "graphql",
                "endpoint": endpoint,
                "operation": operation,
                "mutation": "baseline",
                "baseline_status": baseline_status,
                "status": baseline_status,
            }
        ]
        for name, mutated in _graphql_mutations(variables).items():
            status, body = self._post(
                endpoint,
                query,
                operation,
                mutated,
                headers,
            )
            candidate = _authz_candidate(
                baseline_body,
                body,
                mutated,
            )
            observation = {
                "protocol": "graphql",
                "endpoint": endpoint,
                "operation": operation,
                "mutation": name,
                "baseline_status": baseline_status,
                "mutated_status": status,
                "response_diff": (
                    "changed" if body != baseline_body else "same"
                ),
            }
            if candidate:
                observation["vuln_category"] = "graphql_authz"
                observation["replay_proof"] = {
                    "baseline_status": baseline_status,
                    "mutated_status": status,
                    "endpoint": endpoint,
                    "mutation": name,
                }
            observations.append(observation)
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(
                {"observation_count": len(observations)},
                ensure_ascii=True,
            ),
            observations=tuple(observations),
            artifact_refs=(f"artifact://protocol/{request.action_id}",),
            side_effect_state="known",
        )

    def _post(
        self,
        endpoint: str,
        query: str,
        operation: str,
        variables: dict,
        headers: dict,
    ) -> tuple[int, dict]:
        payload = {
            "query": query,
            "operationName": operation,
            "variables": variables,
        }
        try:
            response = httpx.post(
                endpoint,
                json=payload,
                headers={**headers, "Content-Type": "application/json"},
                timeout=self._timeout,
            )
            body = _as_json(response.text)
            return response.status_code, body
        except httpx.HTTPError as error:
            return 0, {"error": str(error)}


class WebSocketTesterRunner:
    """Sends baseline + tampered frames and compares echo/state changes."""

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout
        self.executions: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.executions.append(request)
        channel = str(
            request.input.get("channel")
            or request.input.get("url")
            or ""
        )
        payload = request.input.get("payload")
        if not channel:
            return ExecutionResult(
                action_id=request.action_id,
                status="failed",
                exit_code=1,
                stderr="websocket tester requires channel",
                side_effect_state="known",
            )
        frames = asyncio.run(
            self._run_channel(channel, payload)
        )
        observations: list[dict[str, Any]] = []
        baseline = frames[0] if frames else None
        for index, frame in enumerate(frames):
            observation = {
                "protocol": "websocket",
                "endpoint": channel,
                "ws_frame_type": "text",
                "frame_index": index,
                "ws_frame_data": frame,
            }
            if (
                index > 0
                and baseline is not None
                and frame != baseline
                and _looks_like_authorization_data(frame)
            ):
                observation["vuln_category"] = "websocket_authz"
                observation["replay_proof"] = {
                    "channel": channel,
                    "baseline": baseline,
                    "mutated": frame,
                }
            observations.append(observation)
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(
                {"observation_count": len(observations)},
                ensure_ascii=True,
            ),
            observations=tuple(observations),
            artifact_refs=(f"artifact://protocol/{request.action_id}",),
            side_effect_state="known",
        )

    async def _run_channel(
        self,
        channel: str,
        payload: Any,
    ) -> list[str]:
        import websockets

        frames: list[str] = []
        async with websockets.connect(
            channel,
            open_timeout=self._timeout,
        ) as websocket:
            await websocket.send(_json_text(payload))
            frames.append(
                str(await asyncio.wait_for(websocket.recv(), timeout=15))
            )
            await websocket.send(_json_text(payload) + "-mutated")
            frames.append(
                str(await asyncio.wait_for(websocket.recv(), timeout=15))
            )
        return frames


def _graphql_mutations(variables: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mutated = dict(variables)
    object_id = _find_id(variables)
    if object_id is not None:
        mutated = _replace_id(mutated, object_id, f"{object_id}-other")
    return {
        "tamper_object_id": mutated,
        "batch_duplicate": {
            **mutated,
            "batch": True,
        },
    }


def _find_id(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("id", "userId", "objectId", "accountId"):
            if key in value:
                return value[key]
        for item in value.values():
            found = _find_id(item)
            if found is not None:
                return found
    return None


def _replace_id(value: Any, old: Any, new: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                new
                if key in ("id", "userId", "objectId", "accountId")
                and item == old
                else _replace_id(item, old, new)
            )
            for key, item in value.items()
        }
    return value


def _authz_candidate(
    baseline: dict,
    mutated: dict,
    variables: dict,
) -> bool:
    target = _find_id(variables)
    if target is None:
        return False
    text = json.dumps(mutated, ensure_ascii=True, default=str)
    baseline_text = json.dumps(baseline, ensure_ascii=True, default=str)
    return (
        text != baseline_text
        and str(target) in text
        and "error" not in mutated
        and "forbidden" not in text.lower()
    )


def _looks_like_authorization_data(frame: str) -> bool:
    lowered = frame.lower()
    return any(
        marker in lowered
        for marker in ("user", "id", "account", "message", "data")
    )


def _as_json(text: str) -> dict:
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {"body": payload}
    except json.JSONDecodeError:
        return {"body": text}


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=True, default=str)
