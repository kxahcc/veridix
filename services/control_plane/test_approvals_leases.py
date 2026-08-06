from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from services.control_plane.app.api import event_stream_generator
from services.control_plane.app.main import create_app


def make_run(client: TestClient) -> str:
    project = client.post("/api/v1/projects", json={"name": "lab"}).json()
    mission = client.post(
        "/api/v1/missions",
        json={
            "project_id": project["project_id"],
            "name": "web",
            "spec": {},
        },
    ).json()
    run = client.post(
        f"/api/v1/missions/{mission['mission_id']}/runs",
        json={
            "mission_id": mission["mission_id"],
            "idempotency_key": "start:1",
        },
    ).json()
    return run["run_id"]


def test_approval_lifecycle_emits_events() -> None:
    with TestClient(create_app(":memory:")) as client:
        run_id = make_run(client)
        requested = client.post(
            f"/api/v1/runs/{run_id}/approvals",
            json={
                "tool_ref": "shell.exec",
                "risk_level": "L3",
                "idempotency_key": "approval:1",
                "reason": "high risk",
            },
        ).json()

        assert requested["state"] == "requested"
        assert requested["risk_level"] == "L3"
        assert requested["budget_reserved"] == 1

        decided = client.post(
            f"/api/v1/approvals/{requested['approval_id']}/decide",
            json={"approved": True, "decided_by": "operator", "reason": "ok"},
        ).json()
        assert decided["state"] == "approved"
        assert decided["decided_by"] == "operator"

        approvals = client.get(f"/api/v1/runs/{run_id}/approvals").json()
        assert len(approvals) == 1
        events = client.get(
            f"/api/v1/runs/{run_id}/events", params={"after": 0}
        ).json()
        assert [event["event_type"] for event in events] == [
            "run.queued",
            "approval.requested",
            "approval.decided",
        ]


def test_approval_request_is_idempotent() -> None:
    with TestClient(create_app(":memory:")) as client:
        run_id = make_run(client)
        body = {
            "tool_ref": "shell.exec",
            "risk_level": "L3",
            "idempotency_key": "approval:same",
        }
        first = client.post(
            f"/api/v1/runs/{run_id}/approvals", json=body
        ).json()
        second = client.post(
            f"/api/v1/runs/{run_id}/approvals", json=body
        ).json()

        assert second["approval_id"] == first["approval_id"]
        events = client.get(
            f"/api/v1/runs/{run_id}/events", params={"after": 0}
        ).json()
        assert [event["event_type"] for event in events] == [
            "run.queued",
            "approval.requested",
        ]


def test_worker_lease_heartbeat_upserts() -> None:
    with TestClient(create_app(":memory:")) as client:
        first = client.post(
            "/api/v1/leases/agent-worker-1/heartbeat",
            json={"lease_seconds": 30},
        ).json()
        second = client.post(
            "/api/v1/leases/agent-worker-1/heartbeat",
            json={"lease_seconds": 60},
        ).json()

        assert first["worker_id"] == "agent-worker-1"
        assert second["lease_until"] > first["lease_until"]
        record = client.app.state.control.get_lease("agent-worker-1")
        assert record is not None
        assert record.last_seen_at == second["last_seen_at"]


def test_event_stream_generator_emits_started_frame() -> None:
    with TestClient(create_app(":memory:")) as client:
        run_id = make_run(client)
        generator = event_stream_generator(client.app.state.events, run_id, 0)

        async def first_frame():
            return await asyncio.wait_for(anext(generator), timeout=2)

        frame = asyncio.run(first_frame())

        assert frame.startswith("data: ")
        assert "run.queued" in frame
