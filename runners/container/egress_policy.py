from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

from .sandbox_spec import SandboxNetwork, SandboxSpec


HOST_GATEWAY_HOSTS = ("host.docker.internal", "gateway.docker.internal")
DOCKER_SOCKET_PATHS = ("/var/run/docker.sock", "/run/docker.sock")


@dataclass(frozen=True)
class EgressDecision:
    allowed: bool
    rule: str
    explanation: str


def check_egress(
    spec: SandboxSpec,
    host: str,
    port: int,
    *,
    deny_hosts: tuple[str, ...] = HOST_GATEWAY_HOSTS,
) -> EgressDecision:
    network = spec.network
    if network.mode == "none":
        return EgressDecision(
            allowed=False,
            rule="network_disabled",
            explanation="sandbox network mode is none",
        )
    if host in deny_hosts:
        return EgressDecision(
            allowed=False,
            rule="host_gateway_denied",
            explanation=f"{host} is a host gateway and is denied",
        )
    if network.deny_private_ranges and _is_private(host):
        return EgressDecision(
            allowed=False,
            rule="private_range_denied",
            explanation=f"{host} is a private address and private ranges are denied",
        )
    if network.mode == "egress_proxy" and network.allow_targets:
        allowed_hosts = {
            urlparse(target).hostname or target for target in network.allow_targets
        }
        if host not in allowed_hosts:
            return EgressDecision(
                allowed=False,
                rule="target_not_allowed",
                explanation=f"{host} is not in the egress allow targets",
            )
    return EgressDecision(
        allowed=True,
        rule="egress_allowed",
        explanation="egress allowed by sandbox network policy",
    )


def check_dns(
    network: SandboxNetwork,
    hostname: str,
) -> EgressDecision:
    if network.dns_policy == "disabled":
        return EgressDecision(
            allowed=False,
            rule="dns_disabled",
            explanation="sandbox dns_policy disables DNS",
        )
    if network.dns_policy == "pinned" and network.allow_targets:
        allowed_hosts = {
            urlparse(target).hostname or target for target in network.allow_targets
        }
        if hostname not in allowed_hosts:
            return EgressDecision(
                allowed=False,
                rule="dns_not_pinned",
                explanation=f"{hostname} is not pinned in the egress allow targets",
            )
    return EgressDecision(
        allowed=True,
        rule="dns_allowed",
        explanation="DNS allowed by sandbox policy",
    )


def check_mount(spec: SandboxSpec, mount_path: str) -> EgressDecision:
    if mount_path in DOCKER_SOCKET_PATHS:
        return EgressDecision(
            allowed=False,
            rule="docker_socket_denied",
            explanation="Docker socket mounts are denied in Veridix sandboxes",
        )
    return EgressDecision(
        allowed=True,
        rule="mount_allowed",
        explanation="mount is allowed by sandbox policy",
    )


def _is_private(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_link_local or address.is_loopback
