from __future__ import annotations

import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import httpx

from .runner_factory import build_worker_runner_factory


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def heartbeat_path() -> Path:
    return (
        Path(os.environ.get("VERIDIX_RUNTIME_DIR", "runtime"))
        / "state"
        / "agent-worker.heartbeat"
    )


def write_heartbeat() -> None:
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "ok",
                "pid": os.getpid(),
                "at": utc_now(),
            }
        ),
        encoding="utf-8",
    )


_stop = threading.Event()


def _beat_loop() -> None:
    while not _stop.wait(1.0):
        write_heartbeat()
        _ping_lease()


def _ping_lease() -> None:
    control_url = os.environ.get("VERIDIX_CONTROL_URL")
    if not control_url:
        return
    try:
        import httpx

        httpx.post(
            f"{control_url}/api/v1/leases/agent-worker/heartbeat",
            json={"lease_seconds": 30},
            timeout=1.0,
            trust_env=False,
        )
    except Exception:
        # Lease reporting must never break the heartbeat loop.
        pass


def _worker_runner_factory():
    return build_worker_runner_factory()


def _autopilot_loop() -> None:
    from services.agent_runtime.control_worker import (
        ControlPlaneClient,
        WorkerOptions,
        run_forever,
    )

    control_url = os.environ.get("VERIDIX_CONTROL_URL")
    if not control_url:
        return
    try:
        print("autopilot: building runner factory", flush=True)
        runner_factory = build_worker_runner_factory()
        print("autopilot: runner factory ready", flush=True)
    except Exception as error:
        print(f"worker autopilot disabled: {error}", flush=True)
        return
    print("autopilot: entering run_forever", flush=True)
    options = WorkerOptions(
        provider_endpoint=os.environ.get("VERIDIX_PROVIDER_ENDPOINT"),
        provider_model=os.environ.get("VERIDIX_PROVIDER_MODEL"),
        api_key_ref=os.environ.get("VERIDIX_PROVIDER_API_KEY_REF"),
        max_turns=int(os.environ.get("VERIDIX_WORKER_MAX_TURNS", "5")),
        max_tokens=int(os.environ.get("VERIDIX_WORKER_MAX_TOKENS", "1024")),
        poll_interval_seconds=float(
            os.environ.get("VERIDIX_WORKER_POLL_INTERVAL", "1.0")
        ),
        streaming=os.environ.get("VERIDIX_WORKER_STREAMING") == "1",
        spool_limit=int(os.environ.get("VERIDIX_WORKER_SPOOL_LIMIT", "1000")),
    )
    run_forever(
        ControlPlaneClient(control_url),
        options,
        runner_factory=runner_factory,
        stop_event=_stop,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _stop.clear()
    write_heartbeat()
    thread = threading.Thread(target=_beat_loop, daemon=True)
    thread.start()
    autopilot_thread = None
    if (
        os.environ.get("VERIDIX_WORKER_AUTOPILOT") == "1"
        and os.environ.get("VERIDIX_CONTROL_URL")
    ):
        autopilot_thread = threading.Thread(
            target=_autopilot_loop,
            daemon=True,
        )
        autopilot_thread.start()
    yield
    _stop.set()


app = FastAPI(
    title="veridix agent runtime",
    version="0.1.0",
    lifespan=lifespan,
)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    pid: int


ALLOWED_INGEST_EVENT_TYPES = frozenset(
    {
        "resource.recovered",
        "resource.lost",
        "browser.rebuilt",
        "proxy.restarted",
        "tool.failed",
        "harness.snapshot",
        "behavior.snapshot",
    }
)


class IngestEventIn(BaseModel):
    event_id: str
    event_type: str
    payload: dict = {}


@app.post("/runs/{run_id}/events")
async def relay_event(run_id: str, body: IngestEventIn) -> dict:
    if body.event_type not in ALLOWED_INGEST_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="event type not allowed")
    control_url = os.environ.get("VERIDIX_CONTROL_URL")
    if not control_url:
        raise HTTPException(
            status_code=503,
            detail="VERIDIX_CONTROL_URL is not configured",
        )
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        response = await client.post(
            f"{control_url}/api/v1/runs/{run_id}/events",
            json={
                "event_id": body.event_id,
                "event_type": body.event_type,
                "actor": "agent-worker",
                "payload": body.payload,
            },
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text,
        )
    return response.json()


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="agent-worker",
        version="0.1.0",
        pid=os.getpid(),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("VERIDIX_AGENT_PORT", "8788")),
        log_level="warning",
    )
