from __future__ import annotations

from typing import Any

from .contracts import (
    CoverageRecord,
    FactRecord,
    LoopState,
    LoopToolResult,
    OracleResult,
    utc_now,
)
from .ports import LoopToolPort, OraclePort


class AuthzMatrixTool(LoopToolPort):
    """Executes one role x endpoint matrix cell.

    Fixture mode: the injected matrix declares the expected authorization
    outcome per (endpoint, role). A cell that returns data when the matrix
    says the role should be denied produces an IDOR finding fact.
    """

    def __init__(
        self,
        matrix: dict[tuple[str, str], str] | None = None,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self._matrix = matrix or {}
        self._outcomes = outcomes or {}
        self.executions: list[Any] = []

    def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
        self.executions.append(proposal)
        endpoint = str(proposal.input.get("endpoint", ""))
        role = str(proposal.input.get("role", ""))
        method = str(proposal.input.get("method", "GET"))
        object_id = str(proposal.input.get("object_id", ""))
        expected = self._matrix.get((endpoint, role), "denied")
        effective = self._outcomes.get((endpoint, role), expected)
        allowed = effective == "allowed"
        observation = {
            "endpoint": endpoint,
            "role": role,
            "method": method,
            "object_id": object_id,
            "baseline_status": 200 if allowed else 403,
            "mutated_status": 200 if allowed else 403,
            "response_diff": "none" if allowed else "forbidden",
            "authz_status": "allowed" if allowed else "denied",
            "expected_denied": expected == "denied",
        }
        facts: list[FactRecord] = []
        evidence_ref = f"evidence://authz/{endpoint}/{role}"
        if allowed and expected == "denied":
            facts.append(
                FactRecord(
                    fact_id=f"fact_authz_{endpoint}_{role}",
                    subject=endpoint,
                    predicate="finding",
                    value="IDOR",
                    source_refs=(evidence_ref,),
                    confidence=0.9,
                    trust="project_observed",
                    metadata=observation,
                )
            )
        elif not allowed and expected == "allowed":
            facts.append(
                FactRecord(
                    fact_id=f"fact_negative_{endpoint}_{role}",
                    subject=endpoint,
                    predicate="negative_finding",
                    value="IDOR",
                    source_refs=(evidence_ref,),
                    confidence=0.9,
                    trust="project_observed",
                    metadata=observation,
                )
            )
        return LoopToolResult(
            status="completed",
            observations=(observation,),
            facts=tuple(facts),
            evidence_refs=(evidence_ref,) if facts else (),
        )


class AuthzMatrixOracle(OraclePort):
    """Verifies when matrix evidence proves object-level authorization bypass."""

    def evaluate(
        self,
        state: LoopState,
        facts: tuple,
        coverage: CoverageRecord,
    ) -> OracleResult:
        findings = [
            fact
            for fact in facts
            if fact.predicate == "finding"
            and str(fact.value) in ("IDOR", "authz_bypass", "business_logic")
            and fact.source_refs
        ]
        if findings:
            return OracleResult(
                status="verified",
                evidence_refs=tuple(
                    sorted(
                        {
                            ref
                            for fact in findings
                            for ref in fact.source_refs
                        }
                    )
                ),
                reason="authz_matrix_evidence",
                metadata={
                    "finding_count": len(findings),
                    "subjects": sorted({fact.subject for fact in findings}),
                },
            )
        negatives = [
            fact
            for fact in facts
            if fact.predicate == "negative_finding"
        ]
        if negatives:
            return OracleResult(
                status="not_verified",
                reason="matrix_covered_no_candidate",
                metadata={"negative_count": len(negatives)},
            )
        return OracleResult(
            status="inconclusive",
            reason="matrix_incomplete",
        )


class SSRFCallbackTool(LoopToolPort):
    """Sends an SSRF candidate to a callback token and reads the result.

    Fixture mode: callbacks maps one-time tokens to evidence; a received
    callback produces a callback_evidence fact with one-time semantics.
    """

    def __init__(self, callbacks: dict[str, dict] | None = None) -> None:
        self._callbacks = callbacks or {}
        self.executions: list[Any] = []

    def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
        self.executions.append(proposal)
        endpoint = str(proposal.input.get("url", ""))
        token = str(proposal.input.get("callback_token", ""))
        evidence = self._callbacks.get(token)
        if evidence is None:
            return LoopToolResult(
                status="completed",
                observations=(
                    {
                        "endpoint": endpoint,
                        "callback": "pending",
                        "callback_token": token,
                    },
                ),
            )
        observation = {
            "endpoint": endpoint,
            "callback": "received",
            "callback_token": token,
            "source": evidence.get("source", "unknown"),
            "one_time": True,
        }
        fact = FactRecord(
            fact_id=f"fact_callback_{token}",
            subject=endpoint,
            predicate="callback_evidence",
            value="verified",
            source_refs=(f"evidence://oast/{token}",),
            confidence=0.95,
            trust="project_observed",
            observed_at=utc_now(),
            metadata=observation,
        )
        return LoopToolResult(
            status="completed",
            observations=(observation,),
            facts=(fact,),
            evidence_refs=(f"evidence://oast/{token}",),
        )


class SSRFCallbackOracle(OraclePort):
    """Verifies only with bound one-time callback evidence."""

    def evaluate(
        self,
        state: LoopState,
        facts: tuple,
        coverage: CoverageRecord,
    ) -> OracleResult:
        evidence = [
            fact
            for fact in facts
            if fact.predicate == "callback_evidence"
            and fact.value == "verified"
            and fact.source_refs
        ]
        if evidence:
            return OracleResult(
                status="verified",
                evidence_refs=tuple(
                    sorted(
                        {
                            ref
                            for fact in evidence
                            for ref in fact.source_refs
                        }
                    )
                ),
                reason="callback_received",
                metadata={
                    "token_count": len(evidence),
                    "subjects": sorted({fact.subject for fact in evidence}),
                },
            )
        return OracleResult(
            status="inconclusive",
            reason="callback_pending",
        )
