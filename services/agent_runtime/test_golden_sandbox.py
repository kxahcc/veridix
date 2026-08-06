from __future__ import annotations

import http.client
import socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

from runners.container.runner_port import FakeSandboxBackend
from runners.container.sandbox_spec import SandboxSpec
from services.agent_runtime.golden import GoldenRunDriver, GoldenRunSpec
from services.agent_runtime.kernel.sandbox_tool_runner import SandboxToolRunner


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_http(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            parsed = urllib.parse.urlsplit(url)
            connection = http.client.HTTPConnection(
                parsed.hostname,
                parsed.port,
                timeout=2.0,
            )
            connection.request("GET", parsed.path or "/")
            response = connection.getresponse()
            response.read()
            connection.close()
            if response.status < 500:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"endpoint did not become ready: {url}")


@pytest.mark.integration
def test_golden_path_routes_tools_through_runner_adapter() -> None:
    root = Path(__file__).resolve().parents[2]
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.lab_provider.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    backend = FakeSandboxBackend()
    try:
        _wait_http(f"http://127.0.0.1:{port}/healthz")
        driver = GoldenRunDriver(
            timeout_seconds=15,
            runner_factory=lambda: SandboxToolRunner(
                backend,
                SandboxSpec(
                    sandbox_profile="S2",
                    image_digest="sha256:golden-lab",
                ),
            ),
        )
        result = driver.run(
            GoldenRunSpec(
                run_id="golden_sandbox_001",
                mission=(
                    "Use the shell.probe tool against the target once, "
                    "then run.finish."
                ),
                target_ref="https://lab.example.test",
                behavior_snapshot="behavior_sandbox_001",
                provider_endpoint=f"http://127.0.0.1:{port}/v1",
                provider_model="veridix-lab-flash",
                max_turns=5,
            )
        )

        assert result.status == "succeeded"
        assert result.oracle_passed is True
        assert len(backend.executions) >= 1
        assert backend.executions[0].tool_ref == "shell.probe"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
