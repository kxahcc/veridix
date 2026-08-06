from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import ExecutionRequest, ExecutionResult


@dataclass
class FakeRunner:
    side_effect_state: str = "known"
    executions: list[ExecutionRequest] = field(default_factory=list)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.executions.append(request)
        target = request.input.get("target", request.tool_ref)
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=f"probe:{target}",
            stderr="",
            artifact_refs=(f"artifact://{request.action_id}/stdout",),
            side_effect_state=self.side_effect_state,
        )
