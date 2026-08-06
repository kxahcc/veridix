from __future__ import annotations

from typing import Any, Callable

from runners.container.sandbox_spec import (
    SandboxFilesystem,
    SandboxNetwork,
    SandboxSpec,
)
from services.agent_runtime.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
)

from .models import ToolDefinition, ToolPackManifest
from .parsing import parse_output


class ToolExecutionPlanner:
    def plan(
        self,
        definition: ToolDefinition,
        arguments: dict[str, Any],
    ) -> list[str]:
        if not definition.command_template:
            raise ValueError(
                f"tool {definition.ref} has no command_template"
            )
        rendered = []
        properties = definition.schema.get("properties", {})
        for part in definition.command_template:
            if part.startswith("{") and part.endswith("}"):
                key = part[1:-1]
                default = properties.get(key, {}).get("default")
                if key not in arguments and default is None:
                    raise ValueError(
                        f"tool {definition.ref} requires argument {key}"
                    )
                rendered.append(
                    str(arguments[key])
                    if key in arguments
                    else str(default)
                )
            else:
                rendered.append(part)
        return rendered


def validate_tool_arguments(
    definition: ToolDefinition,
    arguments: dict[str, Any],
) -> list[str]:
    schema = definition.schema or {}
    properties = schema.get("properties", {})
    errors: list[str] = []
    for key in schema.get("required", []):
        value = arguments.get(key)
        if value is None or value == "":
            errors.append(f"missing required argument {key}")
    for key, value in arguments.items():
        prop = properties.get(key)
        if not prop:
            continue
        expected = prop.get("type")
        if expected == "string" and not isinstance(value, str):
            errors.append(f"argument {key} must be a string")
        elif expected in ("integer", "number") and not isinstance(
            value,
            (int, float),
        ):
            errors.append(f"argument {key} must be numeric")
        elif expected == "boolean" and not isinstance(value, bool):
            errors.append(f"argument {key} must be boolean")
    return errors


class ContainerToolRunner:
    """Executes a container-pack tool through Docker with its pinned image."""

    def __init__(
        self,
        *,
        manifest: ToolPackManifest,
        definition: ToolDefinition,
        backend_factory: Callable[[], Any] | None = None,
        planner: ToolExecutionPlanner | None = None,
        network: str = "",
        mounts: tuple[dict[str, str], ...] = (),
    ) -> None:
        self._manifest = manifest
        self._definition = definition
        self._backend_factory = backend_factory
        self._planner = planner or ToolExecutionPlanner()
        self._network = network
        self._mounts = mounts

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        from runners.container.runner_port import DockerSandboxBackend

        command = self._planner.plan(self._definition, request.input)
        backend = (
            self._backend_factory()
            if self._backend_factory is not None
            else DockerSandboxBackend()
        )
        spec = SandboxSpec(
            sandbox_profile=self._definition.sandbox_profile,
            image_digest=self._manifest.image_ref(),
            uid=0,
            gid=0,
            filesystem=SandboxFilesystem(mounts=self._mounts),
            network=SandboxNetwork(
                mode=self._manifest.network,
                docker_network=self._network,
            ),
        )
        handle = backend.start(spec)
        try:
            result = backend.exec(
                handle,
                ExecutionRequest(
                    action_id=request.action_id,
                    run_id=request.run_id,
                    tool_ref=self._definition.ref,
                    input={
                        "command": command,
                        "cwd": "/workspace/input",
                    },
                    idempotency_key=request.idempotency_key,
                    timeout_seconds=(
                        self._definition.timeout_seconds
                        or request.timeout_seconds
                    ),
                ),
            )
            observations = parse_output(
                self._definition.output_parser,
                result.stdout,
            )
            return ExecutionResult(
                action_id=result.action_id,
                status=result.status,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                artifact_refs=result.artifact_refs,
                side_effect_state=result.side_effect_state,
                observations=observations,
            )
        finally:
            backend.destroy(handle)
