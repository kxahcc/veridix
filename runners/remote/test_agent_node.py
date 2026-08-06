from __future__ import annotations

import os
import socket
import sys
import threading
import time

import httpx
import uvicorn
from fastapi.testclient import TestClient

from services.control_plane.app.main import create_app


def test_agent_node_registers_executes_and_posts_signed_result() -> None:
    from runners.remote.agent_node import main

    app = create_app(":memory:")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    deadline = time.time() + 15
    reached = False
    while time.time() < deadline:
        try:
            if (
                httpx.get(
                    f"http://127.0.0.1:{port}/healthz",
                    timeout=1,
                    trust_env=False,
                ).status_code
                == 200
            ):
                reached = True
                break
        except Exception:
            time.sleep(0.2)
    assert reached, "control-plane server did not become healthy"

    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        timeout=5.0,
        trust_env=False,
    ) as http:
        registered = http.post(
            "/api/v1/remote/nodes",
            json={
                "node_id": "agent-node-test",
                "version": "0.1.0",
                "capabilities": ["local-shell"],
                "public_key": "fixture-pubkey",
            },
        )
        registered.raise_for_status()
        dispatch = http.post(
            "/api/v1/remote/nodes/agent-node-test/dispatch",
            json={
                "task_ref": "task/echo",
                "payload": {"command": ["echo", "hello-node"]},
                "lease_seconds": 300,
            },
        )
        dispatch.raise_for_status()
        assert dispatch.json()["dispatch"]["task_ref"] == "task/echo"

    os.environ["VERIDIX_CONTROL_URL"] = f"http://127.0.0.1:{port}"
    os.environ["VERIDIX_NODE_ID"] = "agent-node-test"
    sys.argv = [
        "agent_node",
        "--control-url",
        f"http://127.0.0.1:{port}",
        "--node-id",
        "agent-node-test",
        "--once",
    ]
    assert main() == 0

    with TestClient(app) as client:
        nodes = client.get("/api/v1/remote/nodes").json()
        assert nodes[0]["node_id"] == "agent-node-test"
        assert nodes[0]["status"] == "online"
        tasks = client.get(
            "/api/v1/remote/nodes/agent-node-test/tasks"
        ).json()
        assert tasks == []
        results = client.get(
            "/api/v1/remote/nodes/agent-node-test/results"
        ).json()
        assert len(results) == 1
        assert results[0]["task_ref"] == "task/echo"
        assert results[0]["status"] == "completed"
        assert results[0]["signature"]
        assert "hello-node" in results[0]["payload"]["stdout"]
    server.should_exit = True
