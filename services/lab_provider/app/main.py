from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel


MODELS = ("veridix-lab-flash", "veridix-lab-pro")
DEFAULT_TARGET = "https://lab.example.test"

router = APIRouter()


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    max_tokens: int | None = None


def _authorized(request: Request) -> bool:
    expected = os.environ.get("LAB_PROVIDER_API_KEY")
    if not expected:
        return True
    return request.headers.get("Authorization") == f"Bearer {expected}"


def _require_authorized(request: Request) -> None:
    if not _authorized(request):
        raise HTTPException(status_code=401, detail="invalid api key")


@router.get("/models")
def list_models() -> dict:
    return {
        "object": "list",
        "data": [
            {"id": model, "object": "model", "owned_by": "veridix-lab"}
            for model in MODELS
        ],
    }


@router.post("/chat/completions")
def chat_completions(body: ChatCompletionRequest, request: Request) -> dict:
    _require_authorized(request)
    message = _next_message(body.messages)
    finish_reason = "tool_calls" if message.get("tool_calls") else "stop"
    return {
        "id": f"chatcmpl-lab-{uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": message,
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "lab-provider"}


def _next_message(messages: list[dict[str, Any]]) -> dict[str, Any]:
    scenario = os.environ.get("LAB_PROVIDER_SCENARIO", "shell")
    if any(message.get("role") == "tool" for message in messages):
        if scenario == "ssrf":
            return _ssrf_next(messages)
        return {
            "role": "assistant",
            "content": (
                "probe complete"
                if scenario == "shell"
                else "scan complete"
            ),
        }
    target = _target_from_messages(messages)
    if scenario == "nikto":
        return _tool_message(
            "web_nikto_scan",
            {"url": target},
        )
    if scenario == "sqlmap":
        return _tool_message(
            "web_sqlmap_scan",
            {"url": target},
        )
    if scenario == "graphql":
        return _tool_message(
            "web_graphql_test",
            {
                "endpoint": f"{target.rstrip('/')}/graphql",
                "query": "query User($id: ID!) { user(id: $id) { id name } }",
                "operation": "User",
                "variables": {"id": "1"},
            },
        )
    if scenario == "websocket":
        return _tool_message(
            "web_websocket_test",
            {
                "channel": target,
                "payload": {
                    "kind": "message",
                    "userId": "1",
                },
            },
        )
    if scenario == "authz":
        return _tool_message(
            "web_authz_test",
            {
                "endpoint": f"{target.rstrip('/')}/api/users/user_2",
                "method": "GET",
                "low_privilege_token": "user-token",
                "high_privilege_token": "admin-token",
                "object_id": "user_2",
            },
        )
    if scenario == "ssrf":
        return _tool_message(
            "oast_create",
            {"purpose": "blind-ssrf"},
        )
    return _tool_message("shell_probe", {"target": target})


def _ssrf_next(messages: list[dict[str, Any]]) -> dict[str, Any]:
    tool_results = [
        message
        for message in messages
        if message.get("role") == "tool"
    ]
    token = _token_from_tool_results(tool_results)
    if len(tool_results) == 1:
        base_url = os.environ.get(
            "LAB_OAST_BASE_URL",
            "http://127.0.0.1:8791",
        )
        return _tool_message(
            "web_ssrf_test",
            {
                "callback_url": (
                    f"{base_url.rstrip('/')}/callback/{token}"
                )
            },
        )
    return _tool_message(
        "oast_check",
        {"token": token},
    )


def _token_from_tool_results(messages: list[dict[str, Any]]) -> str:
    import re

    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            match = re.search(r'"token"\s*:\s*"([^"]+)"', content)
            if match:
                return match.group(1)
    return "oast_unknown"


def _tool_message(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_lab_probe",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(
                        arguments,
                        ensure_ascii=True,
                    ),
                },
            }
        ],
    }


def _target_from_messages(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            match = re.search(r"Target in scope: (\S+)", content)
            if match:
                return match.group(1)
    return DEFAULT_TARGET


def create_app() -> FastAPI:
    application = FastAPI(
        title="veridix local lab provider",
        version="0.1.0",
    )
    application.include_router(router)
    application.include_router(router, prefix="/v1")
    return application


app = create_app()


def main() -> int:
    parser = argparse.ArgumentParser(description="run the local lab provider")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("LAB_PROVIDER_PORT", "8789")),
    )
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
