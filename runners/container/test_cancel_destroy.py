from __future__ import annotations

from runners.container.runner_port import FakeSandboxBackend
from runners.container.sandbox_spec import SandboxSpec
from services.agent_runtime.kernel.contracts import ExecutionRequest


def _spec() -> SandboxSpec:
    return SandboxSpec(
        sandbox_profile="S2",
        image_digest="sha256:abc",
    )


def test_cancel_and_destroy_leave_no_residual_handles() -> None:
    backend = FakeSandboxBackend()
    handle = backend.start(_spec())
    backend.exec(
        handle,
        ExecutionRequest(
            action_id="action_1",
            run_id="run_1",
            tool_ref="shell.exec",
            input={},
            idempotency_key="run_1:shell.exec:1",
        ),
    )

    backend.cancel(handle)
    assert handle.metadata.get("cancelled") is True

    backend.destroy(handle)
    assert backend._resources.count == 0
    assert handle.resource_id not in backend._resources._handles
