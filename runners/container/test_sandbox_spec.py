from __future__ import annotations

import pytest

from runners.container.sandbox_spec import (
    S2_REQUIRED_CONTROLS,
    SandboxValidationError,
    parse_sandbox_spec,
)


def test_parse_valid_spec_and_stable_hash() -> None:
    spec = parse_sandbox_spec(
        {
            "sandbox_profile": "S2",
            "image_digest": "sha256:abc",
            "network": {"mode": "none"},
        }
    )

    assert spec.sandbox_profile == "S2"
    assert spec.uid == 65532
    assert spec.network.mode == "none"
    assert spec.hash() == spec.hash()
    assert S2_REQUIRED_CONTROLS


def test_unknown_network_mode_fails_closed() -> None:
    with pytest.raises(SandboxValidationError, match="unsupported network mode"):
        parse_sandbox_spec(
            {
                "sandbox_profile": "S2",
                "image_digest": "sha256:abc",
                "network": {"mode": "host"},
            }
        )


def test_privileged_mode_is_forbidden() -> None:
    with pytest.raises(SandboxValidationError, match="privileged mode is forbidden"):
        parse_sandbox_spec(
            {
                "sandbox_profile": "S2",
                "image_digest": "sha256:abc",
                "privileged": True,
            }
        )


def test_forbidden_capability_is_rejected() -> None:
    with pytest.raises(SandboxValidationError, match="SYS_ADMIN"):
        parse_sandbox_spec(
            {
                "sandbox_profile": "S2",
                "image_digest": "sha256:abc",
                "capabilities": ["SYS_ADMIN"],
            }
        )


def test_docker_network_is_preserved_in_spec_hash() -> None:
    isolated = parse_sandbox_spec(
        {
            "sandbox_profile": "S2",
            "image_digest": "sha256:abc",
            "network": {"mode": "egress_proxy"},
        }
    )
    lab = parse_sandbox_spec(
        {
            "sandbox_profile": "S2",
            "image_digest": "sha256:abc",
            "network": {
                "mode": "egress_proxy",
                "docker_network": "compose_dvwa-net",
            },
        }
    )

    assert lab.network.docker_network == "compose_dvwa-net"
    assert lab.hash() != isolated.hash()
