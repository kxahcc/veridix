from __future__ import annotations

import json
import os

from fastapi import HTTPException, Request


def require_api_token(request: Request) -> None:
    users_raw = os.environ.get("VERIDIX_CONTROL_USERS")
    if users_raw:
        try:
            users = json.loads(users_raw)
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=500,
                detail="VERIDIX_CONTROL_USERS is not valid JSON",
            ) from error
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        if token not in users:
            raise HTTPException(
                status_code=401,
                detail="invalid or missing api token",
            )
        identity = users[token]
        if not isinstance(identity, dict):
            identity = {"role": "admin"}
        request.state.identity = {
            "token": token,
            **identity,
        }
        return
    expected = os.environ.get("VERIDIX_CONTROL_TOKEN")
    if not expected:
        request.state.identity = {"role": "admin"}
        return
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {expected}":
        raise HTTPException(
            status_code=401,
            detail="invalid or missing api token",
        )
    request.state.identity = {
        "role": os.environ.get("VERIDIX_CONTROL_ROLE", "admin"),
    }
