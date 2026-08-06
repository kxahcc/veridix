from __future__ import annotations

from dataclasses import dataclass

from .models import RemoteNode
from .network_profiles import RemoteNetworkProfile


HIGH_RISK_CAPABILITIES = frozenset(
    {
        "shell",
        "exploit",
        "pty",
        "remote-shell",
        "container-root",
    }
)


@dataclass(frozen=True)
class RemotePolicyDecision:
    allowed: bool
    rule: str
    explanation: str
    risk_level: str = "L1"


@dataclass(frozen=True)
class RemoteExecutionPolicy:
    allowed_high_risk: tuple[str, ...] = ()
    require_approval: bool = True


def check_remote_execution(
    node: RemoteNode,
    *,
    tool_ref: str,
    capability: str,
    policy: RemoteExecutionPolicy,
    network_profile: RemoteNetworkProfile | None = None,
    approval_ref: str | None = None,
) -> RemotePolicyDecision:
    if node.status != "online":
        return RemotePolicyDecision(
            allowed=False,
            rule="node_not_online",
            explanation=f"node {node.node_id} is {node.status}",
        )
    if capability not in node.capabilities:
        return RemotePolicyDecision(
            allowed=False,
            rule="capability_not_declared",
            explanation=f"node {node.node_id} does not declare {capability}",
        )
    if capability in HIGH_RISK_CAPABILITIES:
        if capability not in policy.allowed_high_risk:
            return RemotePolicyDecision(
                allowed=False,
                rule="high_risk_capability_denied",
                explanation=(
                    f"{capability} is high risk and not in the allowed allowlist"
                ),
                risk_level="L3",
            )
        if policy.require_approval and not approval_ref:
            return RemotePolicyDecision(
                allowed=False,
                rule="approval_required",
                explanation="high-risk remote execution requires an approval_ref",
                risk_level="L3",
            )
    if network_profile is not None and network_profile.mode == "none":
        return RemotePolicyDecision(
            allowed=False,
            rule="network_disabled",
            explanation="remote network profile disables egress",
        )
    return RemotePolicyDecision(
        allowed=True,
        rule="remote_allowlist",
        explanation="remote execution allowed by policy",
        risk_level="L2" if capability in HIGH_RISK_CAPABILITIES else "L1",
    )
