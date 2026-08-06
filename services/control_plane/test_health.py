from __future__ import annotations

from fastapi.testclient import TestClient

from services.control_plane.app.main import create_app


def test_control_plane_health() -> None:
    with TestClient(create_app(":memory:")) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "api/control"
