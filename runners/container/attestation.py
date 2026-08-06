from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .sandbox_spec import S2_REQUIRED_CONTROLS

ControlState = str  # "enforced" | "partial" | "unavailable" | "unknown"

ENFORCEMENT_MATRIX: dict[str, dict[str, ControlState]] = {
    "linux": {
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
    "windows": {
        "non_root": "unavailable",
        "read_only_rootfs": "enforced",
        "tmpfs_workspace": "enforced",
        "network_isolated": "enforced",
        "cgroup_limits": "partial",
        "seccomp": "unavailable",
        "no_docker_socket": "enforced",
    },
    "macos": {
        "non_root": "unavailable",
        "read_only_rootfs": "enforced",
        "tmpfs_workspace": "enforced",
        "network_isolated": "enforced",
        "cgroup_limits": "unavailable",
        "seccomp": "unavailable",
        "no_docker_socket": "enforced",
    },
}


@dataclass(frozen=True)
class SandboxAttestation:
    sandbox_id: str
    platform: str
    backend: str
    profile: str
    image_digest: str
    controls: dict[str, ControlState] = field(default_factory=dict)
    effective_uid: int = 65532
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )

    def has_assurance(self, required: tuple[str, ...] = S2_REQUIRED_CONTROLS) -> bool:
        return all(self.controls.get(control) == "enforced" for control in required)

    def missing_controls(
        self, required: tuple[str, ...] = S2_REQUIRED_CONTROLS
    ) -> list[str]:
        return [
            control
            for control in required
            if self.controls.get(control) != "enforced"
        ]


def certify(
    *,
    sandbox_id: str,
    platform: str,
    backend: str,
    profile: str,
    image_digest: str,
    backend_controls: dict[str, ControlState],
    effective_uid: int,
) -> SandboxAttestation:
    matrix = ENFORCEMENT_MATRIX.get(platform.lower(), {})
    controls: dict[str, ControlState] = {}
    for control in S2_REQUIRED_CONTROLS:
        platform_state = matrix.get(control, "unknown")
        reported = backend_controls.get(control, "unknown")
        if reported == "enforced" and platform_state in ("partial", "unavailable"):
            controls[control] = platform_state
        elif reported == "unknown":
            controls[control] = platform_state
        else:
            controls[control] = reported
    return SandboxAttestation(
        sandbox_id=sandbox_id,
        platform=platform,
        backend=backend,
        profile=profile,
        image_digest=image_digest,
        controls=controls,
        effective_uid=effective_uid,
    )


def check_assurance(
    attestation: SandboxAttestation,
    required: tuple[str, ...] = S2_REQUIRED_CONTROLS,
) -> tuple[bool, list[str]]:
    missing = attestation.missing_controls(required)
    return (not missing, missing)
