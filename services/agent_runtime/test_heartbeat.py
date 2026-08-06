from __future__ import annotations

import os

from fastapi.testclient import TestClient

from services.agent_runtime.app.main import app, heartbeat_path


def test_agent_runtime_writes_heartbeat(tmp_path) -> None:
    os.environ["VERIDIX_RUNTIME_DIR"] = str(tmp_path)

    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["service"] == "agent-worker"
        assert heartbeat_path().exists()

    os.environ.pop("VERIDIX_RUNTIME_DIR", None)
