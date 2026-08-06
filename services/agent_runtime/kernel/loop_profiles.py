"""Declarative Loop Profile registry for the security agent kernel.

The design guidance treats a Loop as a versioned, testable profile rather
than an ad-hoc function or a thin set of loop parameters.  These profiles
supply the default contract fields used by Harness projection, worker role
building and Graph persistence while still allowing explicit per-run
overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .contracts import LoopSpec


RISK_LEVELS = ("L0", "L1", "L2", "L3", "L4")
FAILURE_POLICIES = (
    "classify_then_replan",
    "wait_human",
    "stop_and_report",
    "dead_letter",
)
RETRY_POLICIES = (
    "retry_transient_only",
    "retry_with_correction",
    "no_auto_retry",
)


@dataclass(frozen=True)
class LoopProfile:
    name: str
    description: str
    category: str = "domain"
    version: str = "1.0"
    inputs: tuple[str, ...] = ()
    state_schema: str = "generic_loop_state"
    context_policy: str = "node_minimal_with_recent_observations"
    allowed_tools: tuple[str, ...] = ()
    allowed_skills: tuple[str, ...] = ()
    knowledge_query: tuple[str, ...] = ()
    oracle: str = "domain_oracle_required"
    success_criteria: str = "oracle_verified"
    failure_policy: str = "classify_then_replan"
    retry_policy: str = "retry_transient_only"
    budget: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "L1"
    evidence_requirements: tuple[str, ...] = ()
    sandbox_profile: str = "S2"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "inputs": list(self.inputs),
            "state_schema": self.state_schema,
            "context_policy": self.context_policy,
            "allowed_tools": list(self.allowed_tools),
            "allowed_skills": list(self.allowed_skills),
            "knowledge_query": list(self.knowledge_query),
            "oracle": self.oracle,
            "success_criteria": self.success_criteria,
            "failure_policy": self.failure_policy,
            "retry_policy": self.retry_policy,
            "budget": dict(self.budget),
            "risk_level": self.risk_level,
            "evidence_requirements": list(self.evidence_requirements),
            "sandbox_profile": self.sandbox_profile,
        }


def _profile(
    name: str,
    description: str,
    *,
    category: str = "domain",
    inputs: tuple[str, ...] = (),
    state_schema: str = "generic_loop_state",
    context_policy: str = "node_minimal_with_recent_observations",
    allowed_tools: tuple[str, ...] = (),
    allowed_skills: tuple[str, ...] = (),
    knowledge_query: tuple[str, ...] = (),
    oracle: str = "domain_oracle_required",
    success_criteria: str = "oracle_verified",
    failure_policy: str = "classify_then_replan",
    retry_policy: str = "retry_transient_only",
    budget: dict[str, Any] | None = None,
    risk_level: str = "L1",
    evidence_requirements: tuple[str, ...] = (),
    sandbox_profile: str = "S2",
) -> LoopProfile:
    return LoopProfile(
        name=name,
        description=description,
        category=category,
        inputs=inputs,
        state_schema=state_schema,
        context_policy=context_policy,
        allowed_tools=allowed_tools,
        allowed_skills=allowed_skills,
        knowledge_query=knowledge_query,
        oracle=oracle,
        success_criteria=success_criteria,
        failure_policy=failure_policy,
        retry_policy=retry_policy,
        budget=budget or {},
        risk_level=risk_level,
        evidence_requirements=evidence_requirements,
        sandbox_profile=sandbox_profile,
    )


BUILTIN_LOOP_PROFILES: tuple[LoopProfile, ...] = (
    _profile(
        "generic",
        "Safe generic fallback for custom or test-only profiles.",
        category="core",
        oracle="domain_oracle_required",
        success_criteria="oracle_verified_or_inconclusive",
        risk_level="L1",
        evidence_requirements=("observation", "evidence_ref"),
    ),
    _profile(
        "web_discovery",
        "Browser/proxy discovery: endpoint model, coverage and stop on no new observation.",
        inputs=("target_ref", "endpoint_model_ref"),
        state_schema="web_discovery_state",
        allowed_tools=("proxy.list", "browser.open", "proxy.replay"),
        allowed_skills=("web-discovery", "strix-httpx", "strix-ffuf"),
        knowledge_query=("web_surface_recon", "endpoint_model", "owasp_discovery"),
        oracle="coverage_oracle",
        success_criteria="coverage_verified_or_no_new_observation",
        budget={
            "tool_calls": 60,
            "tokens": 32_000,
            "wall_clock_seconds": 300,
            "max_no_progress_iterations": 6,
        },
        risk_level="L1",
        evidence_requirements=("endpoint_observation", "http_transcript", "coverage_record"),
        sandbox_profile="S2",
    ),
    _profile(
        "browser_interaction",
        "Authenticated browser flow observation and state capture.",
        inputs=("target_ref", "browser_session_ref"),
        state_schema="browser_interaction_state",
        allowed_tools=("browser.open", "browser.snapshot", "browser.click"),
        allowed_skills=("web-discovery", "cyberstrikeai-web-attack-methods"),
        knowledge_query=("browser_automation", "web_session_state"),
        oracle="coverage_oracle",
        success_criteria="flow_observed_or_inconclusive",
        budget={
            "tool_calls": 40,
            "tokens": 24_000,
            "wall_clock_seconds": 240,
        },
        risk_level="L2",
        evidence_requirements=("browser_snapshot", "http_transcript", "session_state"),
        sandbox_profile="S2",
    ),
    _profile(
        "verifier",
        "Independent replay/oracle verification with no new exploration tools.",
        inputs=("candidate_findings", "evidence_refs", "auth_context_refs"),
        state_schema="verifier_loop_state",
        allowed_tools=("evidence.replay", "web.replay"),
        allowed_skills=("verifier", "cyberstrikeai-pentest-verification"),
        knowledge_query=("finding_verification", "replay_proof", "evidence_model"),
        oracle="verifier_oracle",
        success_criteria="all_candidates_replayed_or_inconclusive",
        budget={
            "tool_calls": 30,
            "tokens": 20_000,
            "wall_clock_seconds": 180,
        },
        risk_level="L1",
        evidence_requirements=("replay_request", "response_diff", "evidence_refs"),
        sandbox_profile="S2",
    ),
    _profile(
        "hypothesis",
        "Scanner/recon loop that turns target hypotheses into observed facts.",
        inputs=("target_ref", "hypotheses"),
        state_schema="hypothesis_loop_state",
        allowed_tools=(
            "nmap.scan",
            "fscan.scan",
            "nuclei.scan",
            "web.nikto.scan",
            "web.sqlmap.scan",
        ),
        allowed_skills=(
            "strix-nmap",
            "strix-nuclei",
            "strix-sqlmap",
            "web-nikto",
            "veridix-redteam-orchestration",
        ),
        knowledge_query=("target_enumeration", "service_validation", "tool_oracles"),
        oracle="coverage_oracle",
        success_criteria="hypotheses_observed_or_negative_evidence",
        budget={
            "tool_calls": 60,
            "tokens": 32_000,
            "wall_clock_seconds": 300,
        },
        risk_level="L2",
        evidence_requirements=("structured_finding", "tool_output", "target_observation"),
        sandbox_profile="S2",
    ),
    _profile(
        "authz_matrix",
        "Role x endpoint authorization matrix with baseline/mutation oracle.",
        inputs=("endpoint_model_ref", "auth_context_refs"),
        state_schema="authz_matrix_state",
        allowed_tools=("web.authz.test", "proxy.replay", "proxy.compare"),
        allowed_skills=("strix-idor", "cyberstrike-idor-automation"),
        knowledge_query=("authz_oracles", "business_rules", "cwe_authz"),
        oracle="authz_matrix_oracle",
        success_criteria="matrix_covered_or_no_new_candidate",
        budget={
            "tool_calls": 80,
            "tokens": 40_000,
            "wall_clock_seconds": 480,
        },
        risk_level="L2",
        evidence_requirements=("baseline_request", "mutated_request", "response_diff", "auth_context"),
        sandbox_profile="S2",
    ),
    _profile(
        "ssrf_callback",
        "One-time callback SSRF validation through OAST evidence.",
        inputs=("target_ref", "oast_endpoint_ref"),
        state_schema="ssrf_callback_state",
        allowed_tools=("web.ssrf.test", "oast.create", "proxy.replay"),
        allowed_skills=("strix-ssrf", "cyberstrike-ssrf"),
        knowledge_query=("ssrf_oracles", "oast_callback"),
        oracle="ssrf_callback_oracle",
        success_criteria="callback_received_or_exhausted_candidates",
        budget={
            "tool_calls": 50,
            "tokens": 28_000,
            "wall_clock_seconds": 300,
        },
        risk_level="L2",
        evidence_requirements=("callback_token", "oast_callback", "target_request"),
        sandbox_profile="S2",
    ),
    _profile(
        "graphql_test",
        "GraphQL schema discovery, introspection and query mutation testing.",
        inputs=("target_ref", "schema_ref"),
        state_schema="graphql_test_state",
        allowed_tools=("web.graphql.test", "proxy.replay"),
        allowed_skills=("strix-graphql", "cyberstrike-graphql"),
        knowledge_query=("graphql_attack_surface", "graphql_schema"),
        oracle="structured_finding_oracle",
        success_criteria="verified_finding_or_negative_coverage",
        budget={
            "tool_calls": 50,
            "tokens": 28_000,
            "wall_clock_seconds": 300,
        },
        risk_level="L2",
        evidence_requirements=("graphql_query", "response_diff", "schema_snapshot"),
        sandbox_profile="S2",
    ),
    _profile(
        "websocket_test",
        "Realtime WebSocket authorization and message mutation testing.",
        inputs=("target_ref", "ws_connection_ref"),
        state_schema="websocket_test_state",
        allowed_tools=("web.websocket.test", "proxy.replay"),
        allowed_skills=("cyberstrike-websocket",),
        knowledge_query=("websocket_protocol", "realtime_authz"),
        oracle="structured_finding_oracle",
        success_criteria="verified_finding_or_negative_coverage",
        budget={
            "tool_calls": 40,
            "tokens": 24_000,
            "wall_clock_seconds": 240,
        },
        risk_level="L2",
        evidence_requirements=("ws_frames", "auth_context", "response_diff"),
        sandbox_profile="S2",
    ),
    _profile(
        "host_validation",
        "Host/service validation and privilege path discovery.",
        inputs=("target_ref", "host_asset_refs"),
        state_schema="host_validation_state",
        allowed_tools=("nmap.scan", "fscan.scan", "shell.probe"),
        allowed_skills=("strix-nmap", "host.enumeration", "veridix-redteam-orchestration"),
        knowledge_query=("host_enumeration", "service_validation"),
        oracle="structured_finding_oracle",
        success_criteria="verified_finding_or_negative_coverage",
        budget={
            "tool_calls": 70,
            "tokens": 36_000,
            "wall_clock_seconds": 420,
        },
        risk_level="L3",
        evidence_requirements=("host_command", "exit_code", "service_banner", "target_observation"),
        sandbox_profile="S3",
    ),
    _profile(
        "code_scanner",
        "SAST/secret/code-audit scanning with rule-based structured findings.",
        inputs=("repository_ref", "code_rule_refs"),
        state_schema="code_scan_state",
        allowed_tools=("code.sast.semgrep", "code.secrets.detect", "shell.probe"),
        allowed_skills=("strix-semgrep", "cyberstrikeai-source-code-hunting"),
        knowledge_query=("code_audit", "sast_rules", "secret_detection"),
        oracle="structured_finding_oracle",
        success_criteria="verified_finding_or_negative_coverage",
        budget={
            "tool_calls": 60,
            "tokens": 32_000,
            "wall_clock_seconds": 360,
        },
        risk_level="L1",
        evidence_requirements=("rule_id", "match_location", "source_snippet", "tool_version"),
        sandbox_profile="S2",
    ),
    _profile(
        "code_verifier",
        "Verifies code findings with source evidence and minimal replay.",
        inputs=("candidate_findings", "repository_ref"),
        state_schema="code_verifier_state",
        allowed_tools=("evidence.replay", "shell.probe"),
        allowed_skills=("verifier", "cyberstrikeai-pentest-verification"),
        knowledge_query=("finding_verification", "source_evidence"),
        oracle="verifier_oracle",
        success_criteria="all_candidates_replayed_or_inconclusive",
        budget={
            "tool_calls": 30,
            "tokens": 20_000,
            "wall_clock_seconds": 180,
        },
        risk_level="L1",
        evidence_requirements=("replay_proof", "source_snippet", "tool_output"),
        sandbox_profile="S2",
    ),
    _profile(
        "code_to_poc",
        "Turns a code candidate into an executable PoC and verifies it in a sandbox.",
        inputs=("candidate_findings", "repository_ref", "sandbox_ref"),
        state_schema="code_to_poc_state",
        allowed_tools=("shell.probe", "web.replay", "code.sast.semgrep"),
        allowed_skills=("variant-analysis", "cyberstrikeai-web-attack-methods"),
        knowledge_query=("poc_generation", "sandbox_execution"),
        oracle="structured_finding_oracle",
        success_criteria="poc_reproduced_or_inconclusive",
        budget={
            "tool_calls": 50,
            "tokens": 30_000,
            "wall_clock_seconds": 360,
        },
        risk_level="L3",
        evidence_requirements=("poc_request", "sandbox_execution", "oracle_result"),
        sandbox_profile="S3",
    ),
    _profile(
        "reporter",
        "Aggregates verified results into a report with evidence completeness.",
        category="report",
        inputs=("verified_findings", "evidence_refs"),
        state_schema="report_aggregate_state",
        allowed_tools=(),
        allowed_skills=("cyberstrikeai-pentest-output-standards",),
        knowledge_query=("reporting_standards", "risk_scoring"),
        oracle="report_oracle",
        success_criteria="evidence_complete_and_report_ready",
        budget={"tool_calls": 0, "tokens": 8_000},
        risk_level="L0",
        evidence_requirements=("report_bundle", "evidence_refs"),
        sandbox_profile="S2",
    ),
    _profile(
        "research",
        "Benchmark research loop with trajectory and metric recording.",
        category="research",
        inputs=("scenario_ref", "run_ref"),
        state_schema="research_loop_state",
        allowed_tools=(),
        allowed_skills=(),
        knowledge_query=("benchmark_scenario", "experiment_design"),
        oracle="scenario_oracle",
        success_criteria="scenario_completed_with_metrics",
        budget={"tool_calls": 0, "tokens": 12_000},
        risk_level="L0",
        evidence_requirements=("trajectory_ref", "metric_values", "environment_digest"),
        sandbox_profile="S2",
    ),
)


class LoopProfileRegistry:
    def __init__(
        self,
        profiles: tuple[LoopProfile, ...] | list[LoopProfile] | None = None,
    ) -> None:
        source = tuple(profiles) if profiles is not None else BUILTIN_LOOP_PROFILES
        self._profiles = {profile.name: profile for profile in source}

    def get(self, name: str) -> LoopProfile | None:
        return self._profiles.get(name)

    def known(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def as_dict(self) -> dict[str, Any]:
        return {
            name: profile.as_dict()
            for name, profile in sorted(self._profiles.items())
        }


REGISTRY = LoopProfileRegistry()


def apply_loop_profile(spec: LoopSpec) -> LoopSpec:
    """Fill missing declarative LoopSpec fields from the named profile."""
    profile = REGISTRY.get(spec.profile) or REGISTRY.get("generic")
    if profile is None:
        return spec
    return replace(
        spec,
        version=spec.version or profile.version,
        inputs=spec.inputs or profile.inputs,
        state_schema=spec.state_schema or profile.state_schema,
        context_policy=spec.context_policy or profile.context_policy,
        allowed_tools=spec.allowed_tools or profile.allowed_tools,
        allowed_skills=spec.allowed_skills or profile.allowed_skills,
        knowledge_query=spec.knowledge_query or profile.knowledge_query,
        oracle=spec.oracle or profile.oracle,
        success_criteria=spec.success_criteria or profile.success_criteria,
        failure_policy=spec.failure_policy or profile.failure_policy,
        retry_policy=spec.retry_policy or profile.retry_policy,
        risk_level=spec.risk_level or profile.risk_level,
        evidence_requirements=(
            spec.evidence_requirements or profile.evidence_requirements
        ),
        sandbox_profile=spec.sandbox_profile or profile.sandbox_profile,
        budget={**profile.budget, **spec.budget},
    )


def apply_loop_overrides(
    spec: LoopSpec,
    overrides: dict[str, Any] | None,
) -> LoopSpec:
    """Apply task-level per-loop overrides after profile defaults."""
    if not overrides:
        return spec
    kwargs: dict[str, Any] = {}
    for field in (
        "version",
        "inputs",
        "state_schema",
        "context_policy",
        "allowed_tools",
        "allowed_skills",
        "knowledge_query",
        "oracle",
        "success_criteria",
        "failure_policy",
        "retry_policy",
        "risk_level",
        "evidence_requirements",
        "sandbox_profile",
    ):
        if field not in overrides:
            continue
        value = overrides[field]
        if isinstance(value, (list, tuple)):
            value = tuple(value)
        kwargs[field] = value
    if "max_iterations" in overrides:
        kwargs["max_iterations"] = int(overrides["max_iterations"])
    if "stop_on_coverage" in overrides:
        kwargs["stop_on_coverage"] = float(overrides["stop_on_coverage"])
    budget = overrides.get("budget")
    if isinstance(budget, dict):
        kwargs["budget"] = {**spec.budget, **budget}
    if not kwargs:
        return spec
    return replace(spec, **kwargs)


def validate_loop_spec(spec: LoopSpec) -> list[str]:
    """Return contract errors for a profile-applied LoopSpec."""
    errors: list[str] = []
    if not spec.loop_id:
        errors.append("loop_id_required")
    if not spec.profile:
        errors.append("profile_required")
    if not spec.oracle:
        errors.append("oracle_required")
    if not spec.success_criteria:
        errors.append("success_criteria_required")
    if spec.failure_policy not in FAILURE_POLICIES:
        errors.append(f"invalid_failure_policy:{spec.failure_policy or 'empty'}")
    if spec.retry_policy not in RETRY_POLICIES:
        errors.append(f"invalid_retry_policy:{spec.retry_policy or 'empty'}")
    if spec.risk_level not in RISK_LEVELS:
        errors.append(f"invalid_risk_level:{spec.risk_level or 'empty'}")
    if not spec.evidence_requirements:
        errors.append("evidence_requirements_required")
    if not spec.sandbox_profile:
        errors.append("sandbox_profile_required")
    return errors
