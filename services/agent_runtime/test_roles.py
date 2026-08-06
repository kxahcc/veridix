from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    CoverageRecord,
    FactRecord,
    LoopSpec,
    LoopToolResult,
    NodeSpec,
)
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loops import (
    ScriptedLoopModel,
    VerifierOracle,
    VerifierTool,
    WebDiscoveryOracle,
    WebDiscoveryTool,
    action,
    finish,
)
from services.agent_runtime.roles import (
    AgentRole,
    HypothesisCoverageOracle,
    RoleGraphRunner,
    StructuredFindingPolicy,
    StructuredFindingOracle,
    WebDiscoveryOracle,
    build_role_oracle,
    code_audit_role_template,
    redteam_orchestration_role_template,
    scanner_verify_role_template,
    webappsec_role_template,
)
from services.mission_orchestrator.planner import FailureDrivenReplanner


def _role_factory():
    def factory(spec: LoopSpec) -> LoopRunner:
        if spec.profile == "web_discovery":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(
                            ActionProposal(
                                action_id="d1",
                                tool_ref="proxy.list",
                                input={"path": "/"},
                            )
                        ),
                        finish("coverage complete"),
                    ]
                ),
                WebDiscoveryTool(("/", "/admin", "/api/health")),
                WebDiscoveryOracle(),
            )
        if spec.profile == "verifier":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(
                            ActionProposal(
                                action_id="v1",
                                tool_ref="evidence.replay",
                                input={"candidate": "/admin"},
                            )
                        ),
                        finish("verified"),
                    ]
                ),
                VerifierTool({"/admin": "replay://proof/admin"}),
                VerifierOracle(),
            )
        raise AssertionError(spec.profile)

    return factory


def test_role_graph_runs_discovery_verifier_reporter_with_handoffs() -> None:
    runner = RoleGraphRunner(
        roles=webappsec_role_template(target_ref="https://lab.example.test"),
        runner_factory=_role_factory(),
        graph_id="graph_roles",
        mission_ref="mission_roles",
        target_ref="https://lab.example.test",
    )

    result = runner.run()

    statuses = dict(result.node_statuses)
    assert statuses["web_discovery"] == "succeeded"
    assert statuses["verifier"] == "succeeded"
    assert statuses["reporter"] == "succeeded"


def test_failure_driven_replanner_recovers_role_graph() -> None:
    def factory(spec: LoopSpec) -> LoopRunner:
        if spec.profile == "web_discovery":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(
                            ActionProposal(
                                action_id="f1",
                                tool_ref="proxy.list",
                                input={"path": "/"},
                            )
                        ),
                        finish("fallback coverage complete"),
                    ]
                ),
                WebDiscoveryTool(("/",)),
                WebDiscoveryOracle(),
            )
        return LoopRunner(
            spec,
            ScriptedLoopModel([finish("nothing")]),
            WebDiscoveryTool(("/",)),
            WebDiscoveryOracle(),
        )

    fallback = NodeSpec(
        node_id="fallback_scanner",
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id="loop_fallback",
            profile="web_discovery",
            max_iterations=2,
            allowed_tools=("proxy.list",),
            budget={"known_endpoints": ("/",)},
        ),
    )
    runner = RoleGraphRunner(
        roles=(
            AgentRole(
                role_id="scanner",
                profile="fail",
                allowed_tools=("proxy.list",),
            ),
            AgentRole(
                role_id="reporter",
                node_type="aggregate",
                profile="reporter",
            ),
        ),
        runner_factory=factory,
        graph_id="graph_roles_failure",
        mission_ref="mission_roles_failure",
        target_ref="https://lab.example.test",
        planner=FailureDrivenReplanner(
            fallback_node=fallback,
            failed_node="scanner",
            target_node="reporter",
        ),
    )

    result = runner.run()

    statuses = dict(result.node_statuses)
    assert statuses["scanner"] in ("inconclusive", "failed", "dead_letter")
    assert statuses["fallback_scanner"] == "succeeded"
    assert statuses["reporter"] == "succeeded"
    assert result.metrics.replans >= 1


def test_loop_wall_clock_budget_exhaustion_is_visible() -> None:
    spec = LoopSpec(
        loop_id="loop_budget",
        profile="web_discovery",
        max_iterations=5,
        allowed_tools=("proxy.list",),
        budget={"wall_clock_seconds": 0},
    )
    runner = LoopRunner(
        spec,
        ScriptedLoopModel(
            [
                action(
                    ActionProposal(
                        action_id="d1",
                        tool_ref="proxy.list",
                        input={"path": "/"},
                    )
                ),
                finish("done"),
            ]
        ),
        WebDiscoveryTool(("/",)),
        WebDiscoveryOracle(),
    )

    result = runner.run()

    assert result.status == "inconclusive"
    assert result.stop_reason == "budget_exhausted"
    assert any(
        event.event_type == "loop.budget.exhausted"
        for event in runner.events
    )


def test_build_role_oracle_selects_by_profile_and_budget() -> None:
    structured = build_role_oracle(
        "verifier",
        {
            "oracle": "structured_finding",
            "required_categories": ["XSS"],
            "min_severity": "high",
        },
    )
    discovery = build_role_oracle("web_discovery", {})
    hypothesis = build_role_oracle("hypothesis", {})

    assert isinstance(structured, StructuredFindingOracle)
    assert structured.policy.min_severity == "high"
    assert structured.policy.require_evidence is True
    assert isinstance(discovery, WebDiscoveryOracle)
    assert isinstance(hypothesis, HypothesisCoverageOracle)


def test_scanner_verify_template_runs_with_structured_finding() -> None:
    target = "https://lab.example.test"

    class FindingTool:
        def execute(self, proposal, *, idempotency_key):
            return LoopToolResult(
                status="completed",
                observations=({"vuln_category": "XSS"},),
                facts=(
                    FactRecord(
                        fact_id=f"fact_{proposal.action_id}",
                        subject=target,
                        predicate="finding",
                        value="XSS",
                        source_refs=("artifact://scan/1",),
                        metadata={
                            "severity": "high",
                            "matched_at": target,
                        },
                    ),
                ),
                evidence_refs=("artifact://scan/1",),
            )

    def factory(spec):
        script = [
            action(
                ActionProposal(
                    action_id="scan_1",
                    tool_ref="zap.scan",
                    input={"target": target},
                )
            ),
            finish("done"),
        ]
        if spec.profile == "hypothesis":
            return LoopRunner(
                spec,
                ScriptedLoopModel(script),
                FindingTool(),
                HypothesisCoverageOracle(),
            )
        return LoopRunner(
            spec,
            ScriptedLoopModel(script),
            FindingTool(),
            StructuredFindingOracle(required_categories=("XSS",)),
        )

    graph = RoleGraphRunner(
        roles=scanner_verify_role_template(
            target_ref=target,
            required_categories=("XSS",),
        ),
        runner_factory=factory,
        graph_id="graph_scanner",
        mission_ref="mission_scanner",
        target_ref=target,
    )

    result = graph.run()

    statuses = dict(result.node_statuses)
    assert statuses["scanner"] == "succeeded"
    assert statuses["verifier"] == "succeeded"
    assert statuses["reporter"] == "succeeded"
    assert len(result.handoffs) == 2


def test_structured_finding_oracle_filters_by_severity() -> None:
    oracle = StructuredFindingOracle(
        required_categories=("XSS",),
        min_severity="high",
    )
    medium = FactRecord(
        fact_id="f_medium",
        subject="/admin",
        predicate="finding",
        value="XSS",
        metadata={"severity": "medium"},
    )
    high = FactRecord(
        fact_id="f_high",
        subject="/admin",
        predicate="finding",
        value="XSS",
        source_refs=("artifact://scan/1",),
        metadata={
            "severity": "high",
            "matched_at": "/admin",
        },
    )
    coverage = CoverageRecord()

    assert oracle.evaluate(None, (medium,), coverage).status == (
        "not_verified"
    )
    assert oracle.evaluate(None, (high,), coverage).status == "verified"


def test_structured_finding_oracle_requires_verifiable_evidence() -> None:
    oracle = StructuredFindingOracle(
        required_categories=("XSS",),
        min_severity="high",
    )
    bare = FactRecord(
        fact_id="f_bare",
        subject="/admin",
        predicate="finding",
        value="XSS",
        metadata={"severity": "high"},
    )
    proven = FactRecord(
        fact_id="f_proven",
        subject="/admin",
        predicate="finding",
        value="XSS",
        source_refs=("artifact://scan/1",),
        metadata={
            "severity": "high",
            "matched_at": "/admin?x=1",
            "matched_evidence": "alert(1)",
        },
    )
    coverage = CoverageRecord()

    bare_result = oracle.evaluate(None, (bare,), coverage)
    assert bare_result.status == "not_verified"
    assert bare_result.metadata["insufficient_evidence"][0]["missing"] == [
        "evidence"
    ]
    assert oracle.evaluate(None, (proven,), coverage).status == "verified"


def test_structured_finding_oracle_deduplicates_identical_findings() -> None:
    oracle = StructuredFindingOracle(
        required_categories=("XSS",),
        min_severity="high",
    )
    facts = tuple(
        FactRecord(
            fact_id=f"f_dup_{index}",
            subject="/admin",
            predicate="finding",
            value="XSS",
            source_refs=(f"artifact://scan/{index}",),
            metadata={
                "severity": "high",
                "template_id": "xss-template",
                "matched_at": "/admin",
            },
        )
        for index in range(2)
    )

    result = oracle.evaluate(None, facts, CoverageRecord())

    assert result.status == "verified"
    assert result.metadata["deduplicated_count"] == 1
    assert result.metadata["evidence_count"] == 1


def test_structured_finding_oracle_blocks_on_negative_evidence() -> None:
    oracle = StructuredFindingOracle(
        required_categories=("XSS",),
        min_severity="high",
    )
    permissive = StructuredFindingOracle(
        required_categories=("XSS",),
        min_severity="high",
        policy=StructuredFindingPolicy(
            required_categories=("XSS",),
            min_severity="high",
            conflict_blocks=False,
        ),
    )
    facts = (
        FactRecord(
            fact_id="f_proven",
            subject="/admin",
            predicate="finding",
            value="XSS",
            source_refs=("artifact://scan/1",),
            metadata={
                "severity": "high",
                "matched_at": "/admin",
            },
        ),
        FactRecord(
            fact_id="f_negative",
            subject="/admin",
            predicate="negative_finding",
            value="XSS",
            source_refs=("observation://replay/1",),
            metadata={"reason": "replay_no_marker"},
        ),
    )

    blocked = oracle.evaluate(None, facts, CoverageRecord())
    assert blocked.status == "inconclusive"
    assert blocked.reason == "conflicting_negative_evidence"
    assert blocked.metadata["negative_categories"] == ["XSS"]
    assert permissive.evaluate(None, facts, CoverageRecord()).status == (
        "verified"
    )


def test_structured_finding_oracle_requires_metadata_fields() -> None:
    oracle = StructuredFindingOracle(
        required_categories=("XSS",),
        min_severity="high",
        policy=StructuredFindingPolicy(
            required_categories=("XSS",),
            min_severity="high",
            required_metadata_fields=("cwe",),
        ),
    )
    fact = FactRecord(
        fact_id="f_no_cwe",
        subject="/admin",
        predicate="finding",
        value="XSS",
        source_refs=("artifact://scan/1",),
        metadata={
            "severity": "high",
            "matched_at": "/admin",
        },
    )

    result = oracle.evaluate(None, (fact,), CoverageRecord())

    assert result.status == "not_verified"
    assert "cwe" in result.metadata["insufficient_evidence"][0]["missing"]


def test_scanner_verify_template_propagates_finding_policy() -> None:
    roles = scanner_verify_role_template(
        target_ref="https://lab.example.test",
        required_categories=("XSS",),
        min_severity="high",
        require_evidence=True,
        required_metadata_fields=("cwe",),
        dedupe=False,
        conflict_blocks=False,
    )
    verifier = next(role for role in roles if role.role_id == "verifier")
    oracle = build_role_oracle(verifier.profile, verifier.budget)

    assert isinstance(oracle, StructuredFindingOracle)
    assert oracle.policy.required_categories == ("XSS",)
    assert oracle.policy.min_severity == "high"
    assert oracle.policy.required_metadata_fields == ("cwe",)
    assert oracle.policy.dedupe is False
    assert oracle.policy.conflict_blocks is False


def test_scanner_verify_template_accepts_custom_scanner_tools() -> None:
    roles = scanner_verify_role_template(
        target_ref="https://lab.example.test",
        scanner_tools=("web.nikto.scan",),
        required_categories=("Exposure",),
    )
    scanner = next(role for role in roles if role.role_id == "scanner")

    assert scanner.allowed_tools == ("web.nikto.scan",)


def test_redteam_orchestration_template_builds_four_stages() -> None:
    roles = redteam_orchestration_role_template(
        target_ref="https://lab.example.test",
        required_categories=("Exposure",),
        min_severity="low",
    )

    assert [role.role_id for role in roles] == [
        "recon",
        "scanner",
        "verifier",
        "reporter",
    ]
    recon = roles[0]
    scanner = roles[1]
    verifier = roles[2]
    assert recon.allowed_tools == ("nmap.scan", "fscan.scan")
    assert scanner.preconditions == ("https://lab.example.test",)
    assert verifier.preconditions == ("https://lab.example.test",)
    assert isinstance(
        build_role_oracle(verifier.profile, verifier.budget),
        StructuredFindingOracle,
    )


def test_code_audit_template_builds_verifier_with_structured_policy() -> None:
    roles = code_audit_role_template(
        target_ref="/workspace/input/benchmarks/fixtures/code_scan",
        required_categories=("CodeAudit", "HardcodedSecret"),
        min_severity="medium",
        scanner_tools=("code.sast.semgrep", "code.secrets.detect"),
    )
    scanner = next(role for role in roles if role.role_id == "code_scanner")
    verifier = next(role for role in roles if role.role_id == "code_verifier")

    assert scanner.allowed_tools == (
        "code.sast.semgrep",
        "code.secrets.detect",
    )
    assert isinstance(
        build_role_oracle(verifier.profile, verifier.budget),
        StructuredFindingOracle,
    )
    assert verifier.preconditions == (
        "/workspace/input/benchmarks/fixtures/code_scan",
    )


def test_loop_oracle_event_carries_policy_metadata() -> None:
    class FindingTool:
        def execute(self, proposal, *, idempotency_key):
            return LoopToolResult(
                status="completed",
                observations=({"vuln_category": "XSS"},),
                facts=(
                    FactRecord(
                        fact_id="fact_xss",
                        subject="https://lab.example.test",
                        predicate="finding",
                        value="XSS",
                        source_refs=("artifact://scan/1",),
                        metadata={
                            "severity": "high",
                            "matched_at": (
                                "https://lab.example.test/admin"
                            ),
                        },
                    ),
                ),
                evidence_refs=("artifact://scan/1",),
            )

    spec = LoopSpec(
        loop_id="loop_oracle_meta",
        profile="verifier",
        max_iterations=1,
        budget={
            "oracle": "structured_finding",
            "required_categories": ("XSS",),
            "min_severity": "high",
        },
    )
    runner = LoopRunner(
        spec,
        ScriptedLoopModel(
            [
                action(
                    ActionProposal(
                        action_id="s1",
                        tool_ref="zap.scan",
                        input={},
                    )
                ),
                finish("done"),
            ]
        ),
        FindingTool(),
        StructuredFindingOracle(
            required_categories=("XSS",),
            min_severity="high",
        ),
    )

    result = runner.run()

    assert result.status == "succeeded"
    event = next(
        event
        for event in runner.events
        if event.event_type == "loop.oracle.evaluated"
    )
    assert event.payload["status"] == "verified"
    assert event.payload["metadata"]["evidence_count"] == 1
