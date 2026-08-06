from __future__ import annotations

import pytest

from runners.remote.models import NodeRegistration, NodeResult, RemoteNode
from runners.remote.network_profiles import RemoteNetworkProfile
from runners.remote.oast import OastStore, OastTokenError
from runners.remote.policy import (
    RemoteExecutionPolicy,
    check_remote_execution,
)
from runners.remote.transports import TransportSpec, resolve_transport
from runners.remote.registry import RemoteNodeRegistry
from runners.remote.signing import generate_keypair, sign_payload, verify_payload


def test_node_register_heartbeat_and_offline() -> None:
    registry = RemoteNodeRegistry(":memory:")
    registry.register(
        NodeRegistration(
            node_id="node_1",
            version="0.1.0",
            capabilities=("browser", "shell"),
            public_key="pk",
        )
    )

    online = registry.heartbeat("node_1")
    assert online.status == "online"
    assert online.last_seen_at is not None

    offline = registry.mark_offline("node_1")
    assert offline.status == "offline"
    assert registry.list()[0].capabilities == ("browser", "shell")


def test_task_lease_expires() -> None:
    registry = RemoteNodeRegistry(":memory:")
    registry.register(
        NodeRegistration(node_id="node_1", version="1", capabilities=(), public_key="pk")
    )
    lease = registry.lease("node_1", "task_1", lease_seconds=1)

    assert lease.task_ref == "task_1"
    assert registry.expire_leases(now="2999-01-01T00:00:00Z") == 1
    assert registry.expire_leases(now="2999-01-02T00:00:00Z") == 0


def test_dispatch_payload_is_persisted_and_consumed() -> None:
    registry = RemoteNodeRegistry(":memory:")
    registry.register(
        NodeRegistration(
            node_id="node_1",
            version="1",
            capabilities=("local-shell",),
            public_key="pk",
        )
    )
    registry.save_dispatch(
        "node_1",
        "task_dispatch",
        {"tool": "nmap.scan", "args": {"target": "10.0.0.1"}},
        lease_seconds=300,
    )
    pending = registry.pending_tasks("node_1")
    assert len(pending) == 1
    assert pending[0]["task_ref"] == "task_dispatch"
    assert pending[0]["payload"]["tool"] == "nmap.scan"

    registry.save_result(
        NodeResult(
            result_id="res_dispatch",
            node_id="node_1",
            task_ref="task_dispatch",
            status="completed",
            payload={"stdout": "open"},
        )
    )
    assert registry.pending_tasks("node_1") == []


def test_result_signature_verification() -> None:
    private_key, public_key = generate_keypair()
    payload = {"status": "completed", "exit_code": 0}
    signature = sign_payload(payload, private_key)

    assert verify_payload(payload, signature, public_key) is True
    assert verify_payload({"status": "tampered"}, signature, public_key) is False

    registry = RemoteNodeRegistry(":memory:")
    result = registry.save_result(
        NodeResult(
            result_id="res_1",
            node_id="node_1",
            task_ref="task_1",
            status="completed",
            signature=signature,
            payload=payload,
        )
    )
    loaded = registry.get_result("res_1")
    assert loaded.status == "completed"
    assert verify_payload(loaded.payload, loaded.signature, public_key) is True


def test_network_profile_validation() -> None:
    RemoteNetworkProfile(mode="direct").validate()
    RemoteNetworkProfile(mode="tunnel", tunnel_ref="ssh://host").validate()

    with pytest.raises(ValueError, match="unsupported"):
        RemoteNetworkProfile(mode="host").validate()
    with pytest.raises(ValueError, match="tunnel_ref"):
        RemoteNetworkProfile(mode="tunnel").validate()


def test_oast_callback_record_and_find() -> None:
    store = OastStore(":memory:")
    first = store.record(token="cb_token_1", source="http", payload={"path": "/x"})
    second = store.record(token="cb_token_1", source="dns", payload={"query": "x.example"})

    matches = store.find("cb_token_1")

    assert len(matches) == 2
    assert matches[0].callback_id == first.callback_id
    assert matches[1].payload["query"] == "x.example"


def test_oast_one_time_token_redemption() -> None:
    store = OastStore(":memory:")
    token = store.issue_token(source="http", purpose="canary")

    first = store.redeem(token.token, payload={"path": "/callback"})
    assert first.source == "http"

    with pytest.raises(OastTokenError, match="already used"):
        store.redeem(token.token)


def test_oast_expired_token_rejected() -> None:
    store = OastStore(":memory:")
    token = store.issue_token(source="dns", purpose="expired")
    store._conn.execute(
        "UPDATE oast_tokens SET expires_at = '2000-01-01T00:00:00Z' "
        "WHERE token = ?",
        (token.token,),
    )
    store._conn.commit()

    with pytest.raises(OastTokenError, match="expired"):
        store.redeem(token.token)


def test_remote_policy_gates_high_risk_capabilities() -> None:
    node = RemoteNode(
        node_id="node_1",
        version="1",
        capabilities=("shell", "browser"),
        public_key="pk",
        status="online",
    )
    strict = RemoteExecutionPolicy()

    denied = check_remote_execution(
        node,
        tool_ref="shell.exec",
        capability="shell",
        policy=strict,
    )
    assert denied.allowed is False
    assert denied.rule == "high_risk_capability_denied"

    approved = check_remote_execution(
        node,
        tool_ref="shell.exec",
        capability="shell",
        policy=RemoteExecutionPolicy(allowed_high_risk=("shell",)),
        approval_ref="approval_1",
    )
    assert approved.allowed is True

    no_approval = check_remote_execution(
        node,
        tool_ref="shell.exec",
        capability="shell",
        policy=RemoteExecutionPolicy(allowed_high_risk=("shell",)),
    )
    assert no_approval.rule == "approval_required"


def test_remote_policy_denies_offline_and_undeclared() -> None:
    offline = RemoteNode(
        node_id="node_2",
        version="1",
        capabilities=("browser",),
        public_key="pk",
        status="offline",
    )
    decision = check_remote_execution(
        offline,
        tool_ref="browser.open",
        capability="browser",
        policy=RemoteExecutionPolicy(),
    )
    assert decision.rule == "node_not_online"

    undeclared = check_remote_execution(
        RemoteNode(
            node_id="node_3",
            version="1",
            capabilities=("browser",),
            public_key="pk",
            status="online",
        ),
        tool_ref="shell.exec",
        capability="shell",
        policy=RemoteExecutionPolicy(),
    )
    assert undeclared.rule == "capability_not_declared"


def test_transport_specs_validate_and_proxy_url() -> None:
    direct = resolve_transport(TransportSpec(kind="direct", endpoint="127.0.0.1"))
    direct.validate()
    assert direct.http_proxy_url() is None

    http_proxy = resolve_transport(
        TransportSpec(kind="http_proxy", endpoint="127.0.0.1:3128")
    )
    assert http_proxy.http_proxy_url() == "127.0.0.1:3128"

    socks = resolve_transport(
        TransportSpec(kind="socks_proxy", endpoint="127.0.0.1:1080")
    )
    assert socks.http_proxy_url() == "socks5://127.0.0.1:1080"

    with pytest.raises(ValueError, match="tunnel_ref"):
        TransportSpec(kind="ssh_tunnel", endpoint="host").validate()
    with pytest.raises(ValueError, match="unsupported"):
        TransportSpec(kind="carrier_pigeon", endpoint="x").validate()


def test_node_reconnect_lease_and_expiry() -> None:
    registry = RemoteNodeRegistry(":memory:")
    registry.register(
        NodeRegistration(node_id="node_9", version="1", capabilities=(), public_key="pk")
    )
    registry.mark_offline("node_9")

    online = registry.reconnect("node_9", lease_seconds=60)
    assert online.status == "online"
    assert online.last_seen_at is not None

    offline = registry.reconcile_connections(now="2999-01-01T00:00:00Z")
    assert offline == ["node_9"]
    assert registry.get("node_9").status == "offline"
