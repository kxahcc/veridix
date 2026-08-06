from __future__ import annotations

from runners.container.runner_port import RunnerPort
from runners.container.sandbox_spec import SandboxSpec

from .contracts import ExecutionRequest, ExecutionResult


class SandboxToolRunner:
    """Routes reference agent tool calls through a RunnerPort-backed sandbox."""

    def __init__(self, runner_port: RunnerPort, spec: SandboxSpec) -> None:
        self._port = runner_port
        self._spec = spec
        self._handle = None

    def ensure_started(self):
        if self._handle is None:
            self._handle = self._port.start(self._spec)
        return self._handle

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        handle = self.ensure_started()
        return self._port.exec(handle, request)

    def destroy(self) -> None:
        if self._handle is not None:
            self._port.destroy(self._handle)
            self._handle = None
