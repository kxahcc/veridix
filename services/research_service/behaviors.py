from __future__ import annotations

import hashlib
import json

from .models import BehaviorSnapshot


def snapshot_from_components(
    *,
    snapshot_id: str,
    config: dict,
    harness: dict,
    provider: str,
) -> BehaviorSnapshot:
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True).encode("utf-8")
    ).hexdigest()
    harness_digest = hashlib.sha256(
        json.dumps(harness, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return BehaviorSnapshot(
        snapshot_id=snapshot_id,
        config_hash=config_hash,
        harness_digest=harness_digest,
        provider=provider,
    )


def snapshot_from_harness(
    harness,
    *,
    snapshot_id: str,
    provider: str,
) -> BehaviorSnapshot:
    harness_payload = {
        "harness_id": harness.harness_id,
        "node_id": harness.node_id,
        "graph_version": harness.graph_version,
        "target_ref": harness.target_ref,
        "scope_hash": harness.scope_hash,
        "auth_context_ref": harness.auth_context_ref,
        "tool_projection_digest": harness.tool_projection_digest,
        "skill_projection_digest": harness.skill_projection_digest,
        "knowledge_view_digest": harness.knowledge_view_digest,
        "memory_view_digest": harness.memory_view_digest,
        "sandbox_profile": harness.sandbox_profile,
        "network_profile": harness.network_profile,
        "oracle_policy": harness.oracle_policy,
        "stop_policy": harness.stop_policy,
        "budget_policy": harness.budget_policy,
        "provider_capability": harness.provider_capability,
        "builder_version": harness.builder_version,
    }
    config_hash = hashlib.sha256(
        json.dumps(
            {
                "graph_version": harness.graph_version,
                "scope_hash": harness.scope_hash,
                "sandbox_profile": harness.sandbox_profile,
                "network_profile": harness.network_profile,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    harness_digest = hashlib.sha256(
        json.dumps(harness_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return BehaviorSnapshot(
        snapshot_id=snapshot_id,
        config_hash=config_hash,
        harness_digest=harness_digest,
        provider=provider,
    )
