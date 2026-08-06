from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from services.agent_runtime.kernel.contracts import LoopSpec, NodeSpec
from services.agent_runtime.kernel.contracts import (
    CoverageRecord,
    OracleResult,
)
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loop_profiles import (
    apply_loop_overrides,
    apply_loop_profile,
)
from services.agent_runtime.kernel.loops import WebDiscoveryOracle
from services.agent_runtime.kernel.ports import OraclePort
from services.agent_runtime.kernel.security_loops import (
    AuthzMatrixOracle,
    SSRFCallbackOracle,
)
from services.mission_orchestrator.blackboard import Blackboard
from services.mission_orchestrator.contracts import (
    GraphMetrics,
    HandoffPayload,
)
from services.mission_orchestrator.graph_store import GraphStore
from services.mission_orchestrator.planner import PlannerPort
from services.mission_orchestrator.scheduler import GraphScheduler


@dataclass(frozen=True)
class AgentRole:
    role_id: str
    node_type: str = "loop"
    profile: str = "default"
    allowed_tools: tuple[str, ...] = ()
    oracle_ref: str = "domain_oracle_required"
    harness_profile: str = "default"
    preconditions: tuple[str, ...] = ()
    human_prompt: str = ""
    budget: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleGraphResult:
    graph_id: str
    node_statuses: tuple[tuple[str, str], ...]
    handoffs: tuple[HandoffPayload, ...]
    metrics: GraphMetrics
    facts: tuple[Any, ...]
    waiting: bool = False
    waiting_nodes: tuple[str, ...] = ()


class HypothesisCoverageOracle(OraclePort):
    """Verifies when every hypothesis has at least one observed fact."""

    def evaluate(
        self,
        state,
        facts,
        coverage: CoverageRecord,
    ) -> OracleResult:
        observed = {_normalize_endpoint(fact.subject) for fact in facts}
        hypotheses = {
            _normalize_endpoint(hypothesis)
            for hypothesis in state.hypotheses
        }
        if hypotheses and hypotheses.issubset(observed):
            return OracleResult(
                status="verified",
                evidence_refs=tuple(
                    sorted({ref for fact in facts for ref in fact.source_refs})
                ),
                reason="hypotheses_observed",
            )
        return OracleResult(
            status="inconclusive",
            reason="hypotheses_not_observed",
        )


def _normalize_endpoint(value: str) -> str:
    parsed = urlparse(value)
    host = parsed.hostname or value
    host = host.rstrip(".").lower()
    if parsed.port:
        return f"{host}:{parsed.port}"
    return host


class ReplayProofOracle(OraclePort):
    """Verifies when replay proof facts exist for the hypotheses."""

    def evaluate(
        self,
        state,
        facts,
        coverage: CoverageRecord,
    ) -> OracleResult:
        proven = {
            fact.subject
            for fact in facts
            if fact.predicate == "replay_proof"
        }
        if state.hypotheses and set(state.hypotheses).issubset(proven):
            return OracleResult(
                status="verified",
                evidence_refs=tuple(
                    sorted({ref for fact in facts for ref in fact.source_refs})
                ),
                reason="replay_proof_present",
            )
        return OracleResult(
            status="not_verified",
            reason="missing_replay_proof",
        )


@dataclass(frozen=True)
class StructuredFindingPolicy:
    """Verification policy consumed by the structured finding oracle."""

    required_categories: tuple[str, ...] = ()
    min_severity: str = ""
    require_evidence: bool = True
    required_metadata_fields: tuple[str, ...] = ()
    dedupe: bool = True
    conflict_blocks: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_categories": list(self.required_categories),
            "min_severity": self.min_severity,
            "require_evidence": self.require_evidence,
            "required_metadata_fields": list(self.required_metadata_fields),
            "dedupe": self.dedupe,
            "conflict_blocks": self.conflict_blocks,
        }


class StructuredFindingOracle(OraclePort):
    """Verifies structured findings against an evidence policy."""

    def __init__(
        self,
        required_categories: tuple[str, ...] = (),
        min_severity: str = "",
        *,
        policy: StructuredFindingPolicy | None = None,
    ) -> None:
        self._policy = policy or StructuredFindingPolicy(
            required_categories=tuple(required_categories),
            min_severity=str(min_severity).lower(),
        )

    @property
    def policy(self) -> StructuredFindingPolicy:
        return self._policy

    def evaluate(
        self,
        state,
        facts,
        coverage: CoverageRecord,
    ) -> OracleResult:
        policy = self._policy
        required = set(policy.required_categories)
        satisfied: set[str] = set()
        evidence_refs: set[str] = set()
        insufficient: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        duplicates = 0
        for fact in facts:
            if fact.predicate != "finding":
                continue
            category = str(fact.value)
            if required and category not in required:
                continue
            if not self._meets_severity(fact):
                continue
            fingerprint = self._fingerprint(fact)
            if policy.dedupe and fingerprint in seen:
                duplicates += 1
                continue
            seen.add(fingerprint)
            gaps = self._evidence_gaps(fact)
            if gaps:
                insufficient.append(
                    {
                        "category": category,
                        "subject": fact.subject,
                        "fact_id": fact.fact_id,
                        "missing": list(gaps),
                        "source_refs": list(fact.source_refs),
                        "metadata": {
                            key: (fact.metadata or {}).get(key)
                            for key in (
                                "severity",
                                "matched_at",
                                "matched_evidence",
                                "template_id",
                                "rule_id",
                                "vulnerability_id",
                                "replay_proof",
                            )
                            if (fact.metadata or {}).get(key)
                        },
                    }
                )
                continue
            satisfied.add(category)
            evidence_refs.update(fact.source_refs)

        negative = {
            str(fact.value)
            for fact in facts
            if fact.predicate == "negative_finding"
        }
        conflicting = sorted(
            (required & negative) if required else (negative & satisfied)
        )
        if policy.conflict_blocks and conflicting:
            return OracleResult(
                status="inconclusive",
                evidence_refs=tuple(sorted(evidence_refs)),
                reason="conflicting_negative_evidence",
                metadata={
                    "policy": policy.as_dict(),
                    "negative_categories": conflicting,
                },
            )
        if required and not required.issubset(satisfied):
            return OracleResult(
                status="not_verified",
                evidence_refs=tuple(sorted(evidence_refs)),
                reason="missing_structured_findings",
                metadata={
                    "policy": policy.as_dict(),
                    "required_categories": sorted(required),
                    "satisfied_categories": sorted(satisfied),
                    "insufficient_evidence": insufficient,
                    "duplicates": duplicates,
                    "negative_categories": sorted(negative),
                },
            )
        if not satisfied:
            return OracleResult(
                status="not_verified",
                evidence_refs=(),
                reason="missing_structured_findings",
                metadata={
                    "policy": policy.as_dict(),
                    "required_categories": sorted(required),
                    "satisfied_categories": [],
                    "insufficient_evidence": insufficient,
                    "duplicates": duplicates,
                    "negative_categories": sorted(negative),
                },
            )
        return OracleResult(
            status="verified",
            evidence_refs=tuple(sorted(evidence_refs)),
            reason="structured_findings_present",
            metadata={
                "policy": policy.as_dict(),
                "satisfied_categories": sorted(satisfied),
                "evidence_count": len(evidence_refs),
                "deduplicated_count": duplicates,
                "negative_categories": sorted(negative),
            },
        )

    def _evidence_gaps(self, fact: FactRecord) -> tuple[str, ...]:
        gaps: list[str] = []
        metadata = fact.metadata or {}
        if self._policy.require_evidence:
            has_refs = bool(fact.source_refs)
            has_evidence = any(
                bool(metadata.get(key))
                for key in (
                    "matched_at",
                    "matched_evidence",
                    "replay_proof",
                    "evidence",
                )
            )
            if not (has_refs and has_evidence):
                gaps.append("evidence")
        for key in self._policy.required_metadata_fields:
            if not metadata.get(key):
                gaps.append(key)
        return tuple(gaps)

    def _fingerprint(self, fact: FactRecord) -> tuple[str, ...]:
        metadata = fact.metadata or {}
        rule = (
            metadata.get("template_id")
            or metadata.get("rule_id")
            or metadata.get("vulnerability_id")
            or ""
        )
        return (
            str(fact.value),
            str(fact.subject),
            str(rule),
            str(metadata.get("severity") or ""),
        )

    def _meets_severity(self, fact: FactRecord) -> bool:
        if not self._policy.min_severity:
            return True
        metadata = fact.metadata or {}
        severity = str(
            metadata.get("severity")
            or (metadata.get("info") or {}).get("severity")
            or ""
        ).lower()
        return _severity_rank(severity) >= _severity_rank(
            self._policy.min_severity
        )


def _severity_rank(severity: str) -> int:
    return {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    }.get(severity, 0)


def build_role_oracle(profile: str, budget: dict[str, Any]) -> OraclePort:
    if budget.get("oracle") == "structured_finding":
        policy = StructuredFindingPolicy(
            required_categories=tuple(
                budget.get("required_categories", ())
            ),
            min_severity=str(budget.get("min_severity", "")),
            require_evidence=bool(budget.get("require_evidence", True)),
            required_metadata_fields=tuple(
                budget.get("required_metadata_fields", ())
            ),
            dedupe=bool(budget.get("dedupe", True)),
            conflict_blocks=bool(budget.get("conflict_blocks", True)),
        )
        return StructuredFindingOracle(
            policy=policy,
        )
    if profile == "web_discovery":
        return WebDiscoveryOracle()
    if profile == "verifier":
        return ReplayProofOracle()
    if profile == "authz_matrix":
        return AuthzMatrixOracle()
    if profile == "ssrf_callback":
        return SSRFCallbackOracle()
    if profile in ("graphql_test", "websocket_test"):
        return StructuredFindingOracle()
    return HypothesisCoverageOracle()


class RoleGraphRunner:
    """Model-agnostic role graph: roles run on a shared Blackboard."""

    def __init__(
        self,
        *,
        roles: tuple[AgentRole, ...],
        runner_factory: Callable[[LoopSpec], LoopRunner],
        graph_id: str,
        mission_ref: str,
        target_ref: str,
        blackboard: Blackboard | None = None,
        max_retries: int = 2,
        planner: PlannerPort | None = None,
        store: GraphStore | None = None,
        loop_checkpoint_store=None,
        human_resolver=None,
        budget_overrides: dict[str, Any] | None = None,
        loop_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        if not roles:
            raise ValueError("roles must not be empty")
        self._roles = roles
        self._runner_factory = runner_factory
        self._graph_id = graph_id
        self._mission_ref = mission_ref
        self._target_ref = target_ref
        self._blackboard = blackboard or Blackboard(graph_id)
        self._planner = planner
        self._human_resolver = human_resolver
        self._nodes = build_role_nodes(
            roles,
            budget_overrides=budget_overrides,
            loop_overrides=loop_overrides,
        )
        self._edges = build_role_edges(roles)
        self._scheduler = GraphScheduler(
            graph_id=graph_id,
            mission_ref=mission_ref,
            nodes=self._nodes,
            edges=self._edges,
            blackboard=self._blackboard,
            runner_factory=self._runner_factory,
            target_ref=target_ref,
            max_retries=max_retries,
            store=store,
            loop_checkpoint_store=loop_checkpoint_store,
        )

    @property
    def blackboard(self) -> Blackboard:
        return self._blackboard

    def run(self) -> RoleGraphResult:
        while True:
            self._scheduler.run_ready()
            waiting = self._waiting_human_nodes()
            if waiting:
                decisions: dict[str, bool] = {}
                for node_id in waiting:
                    payload = (
                        self._scheduler.state.node_states[node_id]
                        .human_payload
                        or {}
                    )
                    prompt = str(payload.get("prompt") or node_id)
                    decision = (
                        self._human_resolver(node_id, prompt)
                        if self._human_resolver is not None
                        else None
                    )
                    if decision is None:
                        break
                    decisions[node_id] = bool(decision)
                if len(decisions) == len(waiting):
                    for node_id, approved in decisions.items():
                        self._scheduler.resolve_human(
                            node_id,
                            approved=approved,
                            reason="human_resolver",
                        )
                    continue
                unresolved = tuple(
                    node_id
                    for node_id in waiting
                    if node_id not in decisions
                )
                return self._result(
                    waiting=True,
                    waiting_nodes=unresolved,
                )
            if not self._maybe_replan(
                diagnostics=self._scheduler.loop_events()
            ):
                break
        return self._result()

    def _waiting_human_nodes(self) -> tuple[str, ...]:
        return tuple(
            node_id
            for node_id, state in (
                self._scheduler.state.node_states.items()
            )
            if state.status == "waiting_human"
        )

    def _result(
        self,
        *,
        waiting: bool = False,
        waiting_nodes: tuple[str, ...] = (),
    ) -> RoleGraphResult:
        statuses = tuple(
            (state.node_id, state.status)
            for state in self._scheduler.state.node_states.values()
        )
        facts = tuple(
            view.fact for view in self._blackboard.projection()
        )
        return RoleGraphResult(
            graph_id=self._graph_id,
            node_statuses=statuses,
            handoffs=self._scheduler.handoffs,
            metrics=self._scheduler.metrics(),
            facts=facts,
            waiting=waiting,
            waiting_nodes=waiting_nodes,
        )

    def _maybe_replan(
        self,
        *,
        diagnostics: dict[str, list] | None = None,
    ) -> bool:
        if self._planner is None:
            return False
        patch = self._planner.propose(
            self._scheduler.current_snapshot,
            self._blackboard,
            diagnostics=diagnostics,
        )
        if patch is None:
            return False
        self._scheduler.apply_patch(patch)
        return True


def build_role_nodes(
    roles: tuple[AgentRole, ...],
    *,
    sandbox_profile: str = "S2",
    budget_overrides: dict[str, Any] | None = None,
    loop_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, NodeSpec]:
    nodes: dict[str, NodeSpec] = {}
    overrides = budget_overrides or {}
    loop_overrides = loop_overrides or {}
    for role in roles:
        nodes[role.role_id] = NodeSpec(
            node_id=role.role_id,
            node_type=role.node_type,
            allowed_tools=role.allowed_tools,
            harness_profile=role.harness_profile,
            preconditions=role.preconditions,
            oracle_ref=role.oracle_ref,
            sandbox_profile=sandbox_profile,
            human_prompt=role.human_prompt,
            loop_spec=(
                apply_loop_overrides(
                    apply_loop_profile(
                        LoopSpec(
                            loop_id=role.role_id,
                            profile=role.profile,
                            allowed_tools=role.allowed_tools,
                            budget={**role.budget, **overrides},
                        )
                    ),
                    loop_overrides.get(role.role_id, {}),
                )
                if role.node_type == "loop"
                else None
            ),
        )
    return nodes


def build_role_edges(
    roles: tuple[AgentRole, ...],
) -> dict[str, tuple[str, ...]]:
    edges: dict[str, tuple[str, ...]] = {}
    for index, role in enumerate(roles[:-1]):
        edges[role.role_id] = (roles[index + 1].role_id,)
    return edges


def webappsec_role_template(
    *,
    target_ref: str,
    wall_clock_seconds: float = 60.0,
) -> tuple[AgentRole, ...]:
    return (
        AgentRole(
            role_id="web_discovery",
            profile="web_discovery",
            allowed_tools=("proxy.list", "browser.open"),
            oracle_ref="coverage_oracle",
            harness_profile="web_discovery",
            budget={
                "known_endpoints": ("/", "/admin", "/api/health"),
                "wall_clock_seconds": wall_clock_seconds,
            },
        ),
        AgentRole(
            role_id="verifier",
            profile="verifier",
            allowed_tools=("evidence.replay", "web.replay"),
            oracle_ref="verifier_oracle",
            harness_profile="verifier",
            preconditions=("/admin",),
            budget={
                "hypotheses": ("/admin",),
                "wall_clock_seconds": wall_clock_seconds,
            },
        ),
        AgentRole(
            role_id="reporter",
            node_type="aggregate",
            profile="reporter",
            oracle_ref="report_oracle",
            harness_profile="reporter",
        ),
    )


def scanner_verify_role_template(
    *,
    target_ref: str,
    required_categories: tuple[str, ...] = (),
    scanner_tools: tuple[str, ...] = (
        "zap.scan",
        "caido.scan",
        "burp.scan",
    ),
    min_severity: str = "",
    require_evidence: bool = True,
    required_metadata_fields: tuple[str, ...] = (),
    dedupe: bool = True,
    conflict_blocks: bool = True,
    wall_clock_seconds: float = 60.0,
) -> tuple[AgentRole, ...]:
    """Scanner role produces structured findings; verifier confirms them."""
    return (
        AgentRole(
            role_id="scanner",
            profile="hypothesis",
            allowed_tools=scanner_tools,
            oracle_ref="coverage_oracle",
            budget={
                "hypotheses": (target_ref,),
                "wall_clock_seconds": wall_clock_seconds,
            },
        ),
        AgentRole(
            role_id="verifier",
            profile="verifier",
            allowed_tools=("web.replay",),
            oracle_ref="verifier_oracle",
            preconditions=(target_ref,),
            budget={
                "oracle": "structured_finding",
                "required_categories": required_categories,
                "min_severity": min_severity,
                "require_evidence": require_evidence,
                "required_metadata_fields": required_metadata_fields,
                "dedupe": dedupe,
                "conflict_blocks": conflict_blocks,
                "wall_clock_seconds": wall_clock_seconds,
            },
        ),
        AgentRole(
            role_id="reporter",
            node_type="aggregate",
            profile="reporter",
            oracle_ref="report_oracle",
            harness_profile="reporter",
        ),
    )


def redteam_orchestration_role_template(
    *,
    target_ref: str,
    required_categories: tuple[str, ...] = (),
    scanner_tools: tuple[str, ...] = (
        "nuclei.scan",
        "web.nikto.scan",
        "web.sqlmap.scan",
    ),
    min_severity: str = "",
    require_evidence: bool = True,
    required_metadata_fields: tuple[str, ...] = (),
    dedupe: bool = True,
    conflict_blocks: bool = True,
    wall_clock_seconds: float = 600.0,
) -> tuple[AgentRole, ...]:
    """Multi-stage red-team orchestration: recon -> scan -> verify -> report."""
    finding_budget: dict[str, Any] = {
        "oracle": "structured_finding",
        "required_categories": required_categories,
        "min_severity": min_severity,
        "require_evidence": require_evidence,
        "required_metadata_fields": required_metadata_fields,
        "dedupe": dedupe,
        "conflict_blocks": conflict_blocks,
        "wall_clock_seconds": wall_clock_seconds,
    }
    return (
        AgentRole(
            role_id="recon",
            profile="hypothesis",
            allowed_tools=("nmap.scan", "fscan.scan"),
            oracle_ref="coverage_oracle",
            budget={
                "hypotheses": (target_ref,),
                "wall_clock_seconds": wall_clock_seconds,
            },
        ),
        AgentRole(
            role_id="scanner",
            profile="hypothesis",
            allowed_tools=scanner_tools,
            oracle_ref="coverage_oracle",
            preconditions=(target_ref,),
            budget={
                "hypotheses": (target_ref,),
                "wall_clock_seconds": wall_clock_seconds,
            },
        ),
        AgentRole(
            role_id="verifier",
            profile="verifier",
            allowed_tools=("web.replay",),
            oracle_ref="structured_finding_oracle",
            preconditions=(target_ref,),
            budget=finding_budget,
        ),
        AgentRole(
            role_id="reporter",
            node_type="aggregate",
            profile="reporter",
            oracle_ref="report_oracle",
            harness_profile="reporter",
        ),
    )


def code_audit_role_template(
    *,
    target_ref: str,
    required_categories: tuple[str, ...] = (),
    scanner_tools: tuple[str, ...] = (
        "code.sast.semgrep",
        "code.secrets.detect",
    ),
    min_severity: str = "",
    wall_clock_seconds: float = 120.0,
) -> tuple[AgentRole, ...]:
    """SAST/secret-scan roles that verify structured code findings."""
    finding_budget: dict[str, Any] = {
        "oracle": "structured_finding",
        "required_categories": required_categories,
        "min_severity": min_severity,
        "require_evidence": True,
        "required_metadata_fields": ("path", "start_line"),
        "dedupe": True,
        "conflict_blocks": True,
        "wall_clock_seconds": wall_clock_seconds,
    }
    return (
        AgentRole(
            role_id="code_scanner",
            profile="code_scanner",
            allowed_tools=scanner_tools,
            oracle_ref="structured_finding_oracle",
            harness_profile="code_audit",
            budget={
                **finding_budget,
                "hypotheses": (target_ref,),
            },
        ),
        AgentRole(
            role_id="code_verifier",
            profile="code_verifier",
            allowed_tools=scanner_tools,
            oracle_ref="structured_finding_oracle",
            harness_profile="code_audit",
            preconditions=(target_ref,),
            budget=finding_budget,
        ),
        AgentRole(
            role_id="reporter",
            node_type="aggregate",
            profile="reporter",
            oracle_ref="report_oracle",
            harness_profile="reporter",
        ),
    )


def authz_matrix_role_template(
    *,
    target_ref: str,
    allowed_tools: tuple[str, ...] = ("web.authz.test",),
    wall_clock_seconds: float = 120.0,
) -> tuple[AgentRole, ...]:
    """Role-level AuthZ matrix loop backed by replay/mutation tools."""
    return (
        AgentRole(
            role_id="authz_matrix",
            profile="authz_matrix",
            allowed_tools=allowed_tools,
            oracle_ref="authz_matrix_oracle",
            harness_profile="authz",
            budget={
                "wall_clock_seconds": wall_clock_seconds,
            },
        ),
    )


def ssrf_callback_role_template(
    *,
    target_ref: str,
    allowed_tools: tuple[str, ...] = (
        "oast.create",
        "oast.check",
        "web.ssrf.test",
    ),
    wall_clock_seconds: float = 120.0,
) -> tuple[AgentRole, ...]:
    """Role-level SSRF loop backed by one-time OAST callbacks."""
    return (
        AgentRole(
            role_id="ssrf_callback",
            profile="ssrf_callback",
            allowed_tools=allowed_tools,
            oracle_ref="ssrf_callback_oracle",
            harness_profile="ssrf",
            budget={
                "wall_clock_seconds": wall_clock_seconds,
            },
        ),
    )


def graphql_role_template(
    *,
    target_ref: str,
    allowed_tools: tuple[str, ...] = ("web.graphql.test",),
    wall_clock_seconds: float = 120.0,
) -> tuple[AgentRole, ...]:
    return (
        AgentRole(
            role_id="graphql",
            profile="graphql_test",
            allowed_tools=allowed_tools,
            oracle_ref="structured_finding_oracle",
            harness_profile="graphql",
            budget={
                "oracle": "structured_finding",
                "wall_clock_seconds": wall_clock_seconds,
            },
        ),
    )


def websocket_role_template(
    *,
    target_ref: str,
    allowed_tools: tuple[str, ...] = ("web.websocket.test",),
    wall_clock_seconds: float = 120.0,
) -> tuple[AgentRole, ...]:
    return (
        AgentRole(
            role_id="websocket",
            profile="websocket_test",
            allowed_tools=allowed_tools,
            oracle_ref="structured_finding_oracle",
            harness_profile="websocket",
            budget={
                "oracle": "structured_finding",
                "wall_clock_seconds": wall_clock_seconds,
            },
        ),
    )
