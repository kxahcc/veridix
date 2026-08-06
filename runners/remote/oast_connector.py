from __future__ import annotations

import argparse
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .oast import OastStore, OastTokenError


class OastConnector:
    def __init__(self, store: OastStore) -> None:
        self._store = store

    def create_app(self) -> FastAPI:
        application = FastAPI(
            title="veridix OAST connector",
            version="0.1.0",
        )

        @application.get("/healthz")
        def healthz() -> dict:
            return {"status": "ok", "service": "oast-connector"}

        @application.post("/callback/{token}")
        async def callback(token: str, request: Request) -> dict:
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            return self._redeem(token, payload)

        @application.get("/callback/{token}")
        async def callback_get(token: str) -> dict:
            return self._redeem(token, {})

        return application

    def _redeem(self, token: str, payload: dict[str, Any]) -> dict:
        try:
            record = self._store.redeem(
                token,
                source="http",
                payload=_clean(payload),
            )
        except OastTokenError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"accepted": True, "callback_id": record.callback_id}


def _clean(payload: Any) -> dict:
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="run the OAST connector")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("OAST_PORT", "8791")),
    )
    parser.add_argument("--db", default=":memory:")
    args = parser.parse_args()

    import uvicorn

    store = OastStore(args.db)
    app = OastConnector(store).create_app()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
