from __future__ import annotations

from pathlib import Path

import pytest

from services.agent_runtime.kernel.contracts import LoopSpec, NodeSpec
from services.agent_runtime.kernel.harness import (
    HarnessBuilder,
    ProviderCapability,
    SkillEntry,
)
from services.agent_runtime.kernel.loop_profiles import (
    BUILTIN_LOOP_PROFILES,
    REGISTRY,
    apply_loop_overrides,
    apply_loop_profile,
    validate_loop_spec,
)
from services.agent_runtime.roles import build_role_nodes
from services.mission_orchestrator.graph_store import GraphStore


def _bare_spec(profile: str) -> LoopSpec:
    return LoopSpec(loop_id=f"loop_{profile}", profile=profile)


def test_registry_covers_required_loop_domains() -> None:
    known = set(REGISTRY.known())
    required = {
        "generic",
        "web_discovery",
        "browser_interaction",
        "verifier",
        "hypothesis",
        "authz_matrix",
        "ssrf_callback",
        "graphql_test",
        "websocket_test",
        "host_validation",
        "code_scanner",
        "code_verifier",
        "code_to_poc",
        "reporter",
        "research",
    }
    assert required.issubset(known)
    assert len(BUILTIN_LOOP_PROFILES) >= 15


@pytest.mark.parametrize(
    ("profile", "oracle"),
    [
        ("web_discovery", "coverage_oracle"),
        ("verifier", "verifier_oracle"),
        ("authz_matrix", "authz_matrix_oracle"),
        ("ssrf_callback", "ssrf_callback_oracle"),
        ("host_validation", "structured_finding_oracle"),
    ],
)
def test_apply_loop_profile_fills_declarative_fields(
    profile: str,
    oracle: str,
) -> None:
    spec = apply_loop_profile(_bare_spec(profile))
    assert spec.profile == profile
    assert spec.oracle == oracle
    assert spec.success_criteria
    assert spec.failure_policy in {
        "classify_then_replan",
        "wait_human",
        "stop_and_report",
        "dead_letter",
    }
    assert spec.retry_policy in {
        "retry_transient_only",
        "retry_with_correction",
        "no_auto_retry",
    }
    assert spec.risk_level in {"L0", "L1", "L2", "L3", "L4"}
    assert spec.evidence_requirements
    assert spec.sandbox_profile
    assert spec.state_schema
    assert spec.context_policy
    assert spec.knowledge_query


def test_explicit_fields_override_profile_defaults() -> None:
    spec = apply_loop_profile(
        LoopSpec(
            loop_id="loop_explicit",
            profile="web_discovery",
            oracle="custom_oracle",
            risk_level="L3",
            evidence_requirements=("custom_evidence",),
            budget={"tool_calls": 7, "custom_flag": True},
        )
    )
    assert spec.oracle == "custom_oracle"
    assert spec.risk_level == "L3"
    assert spec.evidence_requirements == ("custom_evidence",)
    assert spec.budget["tool_calls"] == 7
    assert spec.budget["custom_flag"] is True
    assert spec.budget["wall_clock_seconds"] > 0


def test_unknown_profile_falls_back_to_generic_contract() -> None:
    spec = apply_loop_profile(_bare_spec("stub"))
    assert spec.profile == "stub"
    assert spec.oracle == "domain_oracle_required"
    assert spec.risk_level == "L1"
    assert spec.sandbox_profile == "S2"
    assert not validate_loop_spec(spec)


def test_apply_loop_overrides_replaces_profile_defaults() -> None:
    spec = apply_loop_overrides(
        apply_loop_profile(_bare_spec("web_discovery")),
        {
            "knowledge_query": ("custom_recon",),
            "allowed_skills": ("custom-skill",),
            "risk_level": "L3",
            "sandbox_profile": "S3",
            "max_iterations": 3,
            "budget": {"tool_calls": 12},
        },
    )
    assert spec.knowledge_query == ("custom_recon",)
    assert spec.allowed_skills == ("custom-skill",)
    assert spec.risk_level == "L3"
    assert spec.sandbox_profile == "S3"
    assert spec.max_iterations == 3
    assert spec.budget["tool_calls"] == 12
    assert spec.budget["wall_clock_seconds"] > 0


def test_validate_loop_spec_reports_missing_contract_fields() -> None:
    errors = validate_loop_spec(
        LoopSpec(
            loop_id="loop_bad",
            profile="web_discovery",
            oracle="",
            failure_policy="unknown",
            retry_policy="unknown",
            risk_level="L9",
            evidence_requirements=(),
            sandbox_profile="",
        )
    )
    assert "oracle_required" in errors
    assert "invalid_failure_policy:unknown" in errors
    assert "invalid_retry_policy:unknown" in errors
    assert "invalid_risk_level:L9" in errors
    assert "evidence_requirements_required" in errors
    assert "sandbox_profile_required" in errors


def test_role_builder_applies_profile_to_loop_nodes() -> None:
    from services.agent_runtime.roles import AgentRole

    nodes = build_role_nodes(
        (
            AgentRole(
                role_id="discovery",
                profile="web_discovery",
                allowed_tools=("proxy.list",),
                oracle_ref="coverage_oracle",
                harness_profile="web_discovery",
            ),
            AgentRole(
                role_id="verifier",
                profile="verifier",
                allowed_tools=("evidence.replay",),
                oracle_ref="verifier_oracle",
                harness_profile="verifier",
            ),
        )
    )
    discovery = nodes["discovery"].loop_spec
    verifier = nodes["verifier"].loop_spec
    assert discovery is not None and discovery.oracle == "coverage_oracle"
    assert discovery.knowledge_query
    assert discovery.evidence_requirements
    assert verifier is not None and verifier.success_criteria
    assert "evidence.replay" in verifier.allowed_tools


def test_role_builder_applies_task_loop_overrides() -> None:
    from services.agent_runtime.roles import AgentRole

    nodes = build_role_nodes(
        (
            AgentRole(
                role_id="discovery",
                profile="web_discovery",
                allowed_tools=("proxy.list",),
                oracle_ref="coverage_oracle",
                harness_profile="web_discovery",
            ),
        ),
        loop_overrides={
            "discovery": {
                "knowledge_query": ("custom_endpoint_model",),
                "allowed_skills": ("custom-web-skill",),
                "budget": {"tool_calls": 9},
            },
        },
    )
    spec = nodes["discovery"].loop_spec
    assert spec is not None
    assert spec.knowledge_query == ("custom_endpoint_model",)
    assert spec.allowed_skills == ("custom-web-skill",)
    assert spec.budget["tool_calls"] == 9


def test_graph_store_round_trips_declarative_loop_spec(tmp_path: Path) -> None:
    store = GraphStore(str(tmp_path / "graph.db"))
    try:
        spec = apply_loop_profile(_bare_spec("authz_matrix"))
        node = NodeSpec(
            node_id="authz",
            node_type="loop",
            loop_spec=spec,
            allowed_tools=("web.authz.test",),
            harness_profile="authz",
            oracle_ref="authz_matrix_oracle",
            knowledge_view="mission",
            sandbox_profile="S2",
        )
        from services.mission_orchestrator.contracts import GraphNodeState

        store.save_snapshot(
            "graph_1",
            version="v1",
            mission_ref="mission_1",
            target_ref="http://target",
            nodes={"authz": node},
            edges={"authz": ()},
            node_states={
                "authz": GraphNodeState(node_id="authz", status="pending")
            },
            handoffs=[],
            updated_at="2026-08-05T00:00:00Z",
        )
        loaded = store.load("graph_1")
        assert loaded is not None
        restored = loaded["nodes"]["authz"].loop_spec
        assert restored is not None
        assert restored.profile == "authz_matrix"
        assert restored.oracle == "authz_matrix_oracle"
        assert restored.success_criteria == (
            "matrix_covered_or_no_new_candidate"
        )
        assert restored.evidence_requirements
        assert restored.sandbox_profile == "S2"
    finally:
        store.close()


def test_harness_projection_uses_loop_profile_skill_allowlist() -> None:
    spec = apply_loop_profile(_bare_spec("web_discovery"))
    node = NodeSpec(
        node_id="discovery",
        node_type="loop",
        loop_spec=spec,
        allowed_tools=(),
        harness_profile="web_discovery",
        oracle_ref="coverage_oracle",
    )
    builder = HarnessBuilder(
        skills={
            "web-discovery": SkillEntry(
                trigger="web_discovery",
                version="2.0",
            ),
            "verifier": SkillEntry(
                trigger="verifier",
                version="1.0",
            ),
        },
    )
    harness, projection = builder.build(
        node,
        ProviderCapability(model_names=("m",), health="ok"),
        target_ref="https://lab.example.test",
        auth_context_ref="auth://fixture",
        scope_hash="scope_hash",
    )
    assert projection.included_skills == ("web-discovery",)
    reasons = {item["name"]: item["reason"] for item in projection.omitted}
    assert reasons["verifier"] == "skill_not_in_loop_scope"
    assert harness.sandbox_profile == "S2"
    assert harness.oracle_policy == "coverage_oracle"
    assert "matrix_covered_or_no_new_candidate" not in harness.stop_policy
