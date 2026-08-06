from __future__ import annotations

from runners.container.attestation import certify, check_assurance


def test_linux_attestation_has_s2_assurance() -> None:
    attestation = certify(
        sandbox_id="sbx_1",
        platform="linux",
        backend="docker",
        profile="S2",
        image_digest="sha256:abc",
        backend_controls={
            "non_root": "enforced",
            "read_only_rootfs": "enforced",
            "tmpfs_workspace": "enforced",
            "network_isolated": "enforced",
            "cgroup_limits": "enforced",
            "seccomp": "enforced",
            "no_docker_socket": "enforced",
        },
        effective_uid=65532,
    )

    ok, missing = check_assurance(attestation)
    assert ok is True
    assert missing == []
    assert attestation.has_assurance() is True


def test_windows_attestation_fails_closed_for_unavailable_controls() -> None:
    attestation = certify(
        sandbox_id="sbx_2",
        platform="windows",
        backend="docker",
        profile="S2",
        image_digest="sha256:abc",
        backend_controls={
            "non_root": "enforced",
            "read_only_rootfs": "enforced",
            "tmpfs_workspace": "enforced",
            "network_isolated": "enforced",
            "cgroup_limits": "enforced",
            "seccomp": "enforced",
            "no_docker_socket": "enforced",
        },
        effective_uid=65532,
    )

    ok, missing = check_assurance(attestation)

    assert ok is False
    assert "non_root" in missing
    assert "seccomp" in missing
    assert attestation.controls["cgroup_limits"] == "partial"
