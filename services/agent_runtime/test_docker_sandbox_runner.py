from __future__ import annotations

import os
import shutil

import pytest

from runners.container.runner_port import DockerSandboxBackend
from runners.container.sandbox_spec import SandboxSpec
from services.agent_runtime.kernel.contracts import ExecutionRequest


@pytest.mark.integration
def test_docker_sandbox_runner_exec_and_destroy() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    image = os.environ.get(
        "VERIDIX_DOCKER_IMAGE",
        "veridix-tools:full",
    )
    backend = DockerSandboxBackend()
    handle = backend.start(
        SandboxSpec(
            sandbox_profile="S2",
            image_digest=image,
            uid=0,
            gid=0,
        )
    )
    try:
        result = backend.exec(
            handle,
            ExecutionRequest(
                action_id="action_docker_1",
                run_id="run_docker_1",
                tool_ref="shell.exec",
                input={"command": ["echo", "docker-ok"]},
                idempotency_key="run_docker_1:shell.exec:1",
            ),
        )
        assert result.status == "completed"
        assert result.exit_code == 0
        assert "docker-ok" in result.stdout
    finally:
        backend.destroy(handle)
        assert backend._resources.count == 0
