from __future__ import annotations

from abc import ABC, abstractmethod

from services.agent_runtime.kernel.contracts import ExecutionRequest, ExecutionResult

from .attestation import SandboxAttestation, certify
from .resource_handle import ResourceHandle, ResourceManager
from .sandbox_spec import SandboxSpec


class RunnerPort(ABC):
    @abstractmethod
    def start(self, spec: SandboxSpec) -> ResourceHandle:
        raise NotImplementedError

    @abstractmethod
    def exec(self, handle: ResourceHandle, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError

    @abstractmethod
    def cancel(self, handle: ResourceHandle) -> None:
        raise NotImplementedError

    @abstractmethod
    def destroy(self, handle: ResourceHandle) -> None:
        raise NotImplementedError

    @abstractmethod
    def attest(self, handle: ResourceHandle) -> SandboxAttestation:
        raise NotImplementedError


class FakeSandboxBackend(RunnerPort):
    def __init__(self) -> None:
        self._resources = ResourceManager()
        self.executions: list[ExecutionRequest] = []

    def start(self, spec: SandboxSpec) -> ResourceHandle:
        handle = self._resources.create(f"sandbox_{self._resources.count + 1}", "s2")
        handle.metadata["spec_hash"] = spec.hash()
        self._resources.mark_ready(handle.resource_id)
        self._resources.attach(handle.resource_id)
        return handle

    def exec(self, handle: ResourceHandle, request: ExecutionRequest) -> ExecutionResult:
        self.executions.append(request)
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=f"sandbox:{request.tool_ref}",
            stderr="",
            artifact_refs=(f"artifact://{request.action_id}/stdout",),
        )

    def cancel(self, handle: ResourceHandle) -> None:
        handle.metadata["cancelled"] = True

    def destroy(self, handle: ResourceHandle) -> None:
        self._resources.destroy(handle.resource_id)

    def attest(self, handle: ResourceHandle) -> SandboxAttestation:
        spec_hash = handle.metadata.get("spec_hash", "unknown")
        return certify(
            sandbox_id=handle.resource_id,
            platform="linux",
            backend="fake",
            profile="S2",
            image_digest=spec_hash,
            backend_controls={
                control: "enforced"
                for control in (
                    "non_root",
                    "read_only_rootfs",
                    "tmpfs_workspace",
                    "network_isolated",
                    "cgroup_limits",
                    "seccomp",
                    "no_docker_socket",
                )
            },
            effective_uid=65532,
        )


class DockerSandboxBackend(RunnerPort):
    """Minimal S2 Docker backend. Requires a running Docker daemon."""

    def __init__(self) -> None:
        import docker

        self._client = docker.from_env()
        self._resources = ResourceManager()
        self._containers: dict[str, object] = {}

    def start(self, spec: SandboxSpec) -> ResourceHandle:
        handle = self._resources.create(f"sandbox_{self._resources.count + 1}", "s2")
        network = (
            spec.network.docker_network
            or ("none" if spec.network.mode == "none" else None)
        )
        volumes = {
            mount["source"]: {
                "bind": mount["target"],
                "mode": mount.get("mode", "ro"),
            }
            for mount in spec.filesystem.mounts
        }
        container = self._client.containers.run(
            spec.image_digest,
            user=f"{spec.uid}:{spec.gid}",
            read_only=spec.filesystem.rootfs == "read_only",
            tmpfs={
                "/workspace": f"rw,size={spec.resources.memory_mb}m",
                "/tmp": "rw,size=256m",
                "/var/tmp": "rw,size=256m",
            },
            volumes=volumes or None,
            network=network,
            mem_limit=f"{spec.resources.memory_mb}m",
            nano_cpus=int(spec.resources.cpus * 1_000_000_000),
            pids_limit=spec.resources.pids,
            detach=True,
            command=["sleep", "infinity"],
        )
        self._containers[handle.resource_id] = container
        handle.metadata["container_id"] = container.id
        self._resources.mark_ready(handle.resource_id)
        self._resources.attach(handle.resource_id)
        return handle

    def exec(self, handle: ResourceHandle, request: ExecutionRequest) -> ExecutionResult:
        container = self._containers[handle.resource_id]
        command = request.input.get("command", ["true"])
        result = container.exec_run(
            command,
            workdir=request.input.get("cwd") or None,
        )
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=result.exit_code,
            stdout=(result.output or b"").decode("utf-8", errors="replace"),
            stderr="",
            artifact_refs=(f"artifact://{request.action_id}/stdout",),
        )

    def cancel(self, handle: ResourceHandle) -> None:
        self._containers[handle.resource_id].kill()

    def destroy(self, handle: ResourceHandle) -> None:
        container = self._containers.pop(handle.resource_id, None)
        if container is not None:
            container.remove(force=True)
        self._resources.destroy(handle.resource_id)

    def attest(self, handle: ResourceHandle) -> SandboxAttestation:
        container = self._containers[handle.resource_id]
        inspect = container.attrs
        host_config = inspect.get("HostConfig", {})
        return certify(
            sandbox_id=handle.resource_id,
            platform="windows",
            backend="docker",
            profile="S2",
            image_digest=inspect.get("Image", ""),
            backend_controls={
                "non_root": "enforced" if inspect.get("Config", {}).get("User") else "unavailable",
                "read_only_rootfs": "enforced" if inspect.get("Config", {}).get("ReadonlyRootfs") else "unavailable",
                "tmpfs_workspace": "enforced" if host_config.get("Tmpfs") else "unavailable",
                "network_isolated": "enforced" if host_config.get("NetworkMode") == "none" else "unavailable",
                "cgroup_limits": "enforced"
                if host_config.get("Memory") and host_config.get("NanoCpus")
                else "unavailable",
                "seccomp": "unknown",
                "no_docker_socket": "unknown",
            },
            effective_uid=65532,
        )
