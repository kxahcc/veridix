from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

SUPPORTED_NETWORK_MODES = ("none", "egress_proxy", "direct")
SUPPORTED_DNS_POLICIES = ("pinned", "disabled")
FORBIDDEN_CAPABILITIES = {
    "SYS_ADMIN",
    "NET_ADMIN",
    "NET_RAW",
    "SYS_PTRACE",
    "SYS_MODULE",
    "SYS_BOOT",
    "SYS_TIME",
    "SYS_RAWIO",
    "DAC_OVERRIDE",
}
PROFILE_ASSURANCE = {
    "S0": "structured",
    "S1": "process",
    "S2": "container",
    "S3": "hardened",
    "S4": "dedicated",
}

S2_REQUIRED_CONTROLS = (
    "non_root",
    "read_only_rootfs",
    "tmpfs_workspace",
    "network_isolated",
    "cgroup_limits",
    "seccomp",
    "no_docker_socket",
)


class SandboxValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SandboxFilesystem:
    rootfs: str = "read_only"
    workspace: str = "ephemeral"
    mounts: tuple[dict[str, str], ...] = ()
    output_paths: tuple[str, ...] = ("/workspace/output",)


@dataclass(frozen=True)
class SandboxNetwork:
    mode: str = "none"
    allow_targets: tuple[str, ...] = ()
    deny_private_ranges: bool = True
    dns_policy: str = "pinned"
    docker_network: str = ""


@dataclass(frozen=True)
class SandboxResources:
    cpus: float = 1.0
    memory_mb: int = 512
    pids: int = 256
    disk_mb: int = 4096
    log_mb: int = 100


@dataclass(frozen=True)
class SandboxSecrets:
    inject: tuple[str, ...] = ()
    redact_on_output: bool = True


@dataclass(frozen=True)
class SandboxLifecycle:
    reuse_scope: str = "agent_session"
    idle_timeout_seconds: int = 900
    destroy_after_run: bool = True


@dataclass(frozen=True)
class SandboxSpec:
    sandbox_profile: str
    image_digest: str
    uid: int = 65532
    gid: int = 65532
    filesystem: SandboxFilesystem = field(default_factory=SandboxFilesystem)
    network: SandboxNetwork = field(default_factory=SandboxNetwork)
    resources: SandboxResources = field(default_factory=SandboxResources)
    secrets: SandboxSecrets = field(default_factory=SandboxSecrets)
    lifecycle: SandboxLifecycle = field(default_factory=SandboxLifecycle)
    capabilities: tuple[str, ...] = ()

    def hash(self) -> str:
        canonical = json.dumps(
            {
                "profile": self.sandbox_profile,
                "image_digest": self.image_digest,
                "uid": self.uid,
                "gid": self.gid,
                "filesystem": self.filesystem.__dict__,
                "network": self.network.__dict__,
                "resources": self.resources.__dict__,
                "secrets": self.secrets.__dict__,
                "lifecycle": self.lifecycle.__dict__,
            },
            sort_keys=True,
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def parse_sandbox_spec(data: dict[str, Any]) -> SandboxSpec:
    profile = data.get("sandbox_profile")
    image = data.get("image_digest")
    if not profile or not image:
        raise SandboxValidationError("sandbox_profile and image_digest are required")
    if profile not in PROFILE_ASSURANCE:
        raise SandboxValidationError(f"unknown sandbox_profile {profile}")
    if data.get("privileged") is True:
        raise SandboxValidationError("privileged mode is forbidden in Veridix defaults")

    capabilities = tuple(data.get("capabilities", ()))
    forbidden = sorted(set(capabilities) & FORBIDDEN_CAPABILITIES)
    if forbidden:
        raise SandboxValidationError(
            f"forbidden capabilities: {', '.join(forbidden)}; use S3/S4 Capability Pack"
        )

    fs_data = data.get("filesystem", {})
    network_data = data.get("network", {})
    resources_data = data.get("resources", {})
    secrets_data = data.get("secrets", {})
    lifecycle_data = data.get("lifecycle", {})

    network_mode = network_data.get("mode", "none")
    if network_mode not in SUPPORTED_NETWORK_MODES:
        raise SandboxValidationError(f"unsupported network mode {network_mode}")
    dns_policy = network_data.get("dns_policy", "pinned")
    if dns_policy not in SUPPORTED_DNS_POLICIES:
        raise SandboxValidationError(f"unsupported dns_policy {dns_policy}")

    return SandboxSpec(
        sandbox_profile=profile,
        image_digest=image,
        uid=int(data.get("uid", 65532)),
        gid=int(data.get("gid", 65532)),
        filesystem=SandboxFilesystem(
            rootfs=fs_data.get("rootfs", "read_only"),
            workspace=fs_data.get("workspace", "ephemeral"),
            mounts=tuple(fs_data.get("mounts", ())),
            output_paths=tuple(fs_data.get("output_paths", ("/workspace/output",))),
        ),
        network=SandboxNetwork(
            mode=network_mode,
            allow_targets=tuple(network_data.get("allow_targets", ())),
            deny_private_ranges=network_data.get("deny_private_ranges", True),
            dns_policy=dns_policy,
            docker_network=str(network_data.get("docker_network", "")),
        ),
        resources=SandboxResources(
            cpus=float(resources_data.get("cpus", 1.0)),
            memory_mb=int(resources_data.get("memory_mb", 512)),
            pids=int(resources_data.get("pids", 256)),
            disk_mb=int(resources_data.get("disk_mb", 4096)),
            log_mb=int(resources_data.get("log_mb", 100)),
        ),
        secrets=SandboxSecrets(
            inject=tuple(secrets_data.get("inject", ())),
            redact_on_output=secrets_data.get("redact_on_output", True),
        ),
        lifecycle=SandboxLifecycle(
            reuse_scope=lifecycle_data.get("reuse_scope", "agent_session"),
            idle_timeout_seconds=int(lifecycle_data.get("idle_timeout_seconds", 900)),
            destroy_after_run=lifecycle_data.get("destroy_after_run", True),
        ),
        capabilities=capabilities,
    )
