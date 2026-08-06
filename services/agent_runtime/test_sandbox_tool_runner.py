from __future__ import annotations

from runners.container.runner_port import FakeSandboxBackend
from runners.container.sandbox_spec import SandboxSpec
from services.agent_runtime.kernel.contracts import ExecutionRequest
from services.agent_runtime.kernel.sandbox_tool_runner import SandboxToolRunner


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        action_id="action_1",
        run_id="run_1",
        tool_ref="shell.exec",
        input={"command": ["echo", "hi"]},
        idempotency_key="run_1:shell.exec:1",
    )


def test_sandbox_tool_runner_routes_through_runner_port() -> None:
    backend = FakeSandboxBackend()
    runner = SandboxToolRunner(
        backend,
        SandboxSpec(sandbox_profile="S2", image_digest="sha256:abc"),
    )

    result = runner.execute(_request())

    assert result.status == "completed"
    assert result.stdout == "sandbox:shell.exec"
    assert len(backend.executions) == 1

    runner.destroy()
    assert backend._resources.count == 0
