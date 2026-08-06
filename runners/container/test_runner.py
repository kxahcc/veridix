from __future__ import annotations

from services.agent_runtime.kernel.contracts import ExecutionRequest
from runners.container.runner_port import FakeSandboxBackend
from runners.container.sandbox_spec import parse_sandbox_spec


def test_fake_runner_start_exec_destroy_and_attest() -> None:
    backend = FakeSandboxBackend()
    spec = parse_sandbox_spec(
        {
            "sandbox_profile": "S2",
            "image_digest": "sha256:abc",
        }
    )

    handle = backend.start(spec)
    result = backend.exec(
        handle,
        ExecutionRequest(
            action_id="action_1",
            run_id="run_1",
            tool_ref="shell.exec",
            input={"command": ["echo", "ok"]},
            idempotency_key="run_1:shell.exec:1",
        ),
    )
    attestation = backend.attest(handle)
    backend.cancel(handle)
    backend.destroy(handle)

    assert result.exit_code == 0
    assert result.stdout == "sandbox:shell.exec"
    assert attestation.has_assurance() is True
    assert handle.metadata.get("cancelled") is True
