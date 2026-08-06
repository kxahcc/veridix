from __future__ import annotations

from runners.container.egress_policy import (
    check_dns,
    check_egress,
    check_mount,
)
from runners.container.sandbox_spec import SandboxNetwork, SandboxSpec


def _spec(network: SandboxNetwork) -> SandboxSpec:
    return SandboxSpec(
        sandbox_profile="S2",
        image_digest="sha256:abc",
        network=network,
    )


def test_egress_denies_none_host_gateway_private_and_unlisted() -> None:
    spec = _spec(SandboxNetwork(mode="none"))
    assert check_egress(spec, "example.com", 443).rule == "network_disabled"

    proxy = _spec(
        SandboxNetwork(
            mode="egress_proxy",
            allow_targets=("https://lab.example.test",),
        )
    )
    assert check_egress(proxy, "host.docker.internal", 80).rule == "host_gateway_denied"
    assert check_egress(proxy, "10.0.0.5", 80).rule == "private_range_denied"
    assert check_egress(proxy, "evil.example", 443).rule == "target_not_allowed"
    assert check_egress(proxy, "lab.example.test", 443).allowed is True


def test_dns_and_mount_policy() -> None:
    disabled = SandboxNetwork(mode="egress_proxy", dns_policy="disabled")
    assert check_dns(disabled, "lab.example.test").rule == "dns_disabled"

    pinned = SandboxNetwork(
        mode="egress_proxy",
        dns_policy="pinned",
        allow_targets=("https://lab.example.test",),
    )
    assert check_dns(pinned, "evil.example").rule == "dns_not_pinned"
    assert check_dns(pinned, "lab.example.test").allowed is True

    spec = _spec(SandboxNetwork())
    assert check_mount(spec, "/var/run/docker.sock").rule == "docker_socket_denied"
    assert check_mount(spec, "/workspace").allowed is True
