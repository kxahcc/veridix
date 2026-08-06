from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import (
    ActionProposal,
    CoverageRecord,
    FactRecord,
    LoopState,
    LoopToolResult,
    ModelDecision,
    OracleResult,
    utc_now,
)
from .ports import LoopModelPort, LoopToolPort, OraclePort


@dataclass
class ScriptedLoopModel(LoopModelPort):
    script: list[ModelDecision]
    calls: int = field(default=0)

    def propose(self, state: LoopState, context: dict) -> ModelDecision:
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return item


def action(proposal: ActionProposal) -> ModelDecision:
    return ModelDecision(kind="action", action=proposal)


def finish(reasoning: str = "") -> ModelDecision:
    return ModelDecision(kind="finish", reasoning=reasoning)


class WebDiscoveryTool(LoopToolPort):
    def __init__(self, endpoints: tuple[str, ...]) -> None:
        self._endpoints = endpoints
        self.executions: list[ActionProposal] = []

    def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
        self.executions.append(proposal)
        if proposal.tool_ref == "proxy.list":
            facts = tuple(
                FactRecord(
                    fact_id=f"fact_discovery_{i}",
                    subject=endpoint,
                    predicate="exposed",
                    value="candidate",
                    source_refs=(f"observation://proxy.list/{endpoint}",),
                )
                for i, endpoint in enumerate(self._endpoints)
            )
            return LoopToolResult(
                status="completed",
                observations=[
                    {"endpoint": endpoint, "method": "GET", "status": 200}
                    for endpoint in self._endpoints
                ],
                facts=facts,
                evidence_refs=("evidence://proxy.list/200",),
            )
        return LoopToolResult(status="denied", error="tool_not_allowed")


class WebDiscoveryOracle(OraclePort):
    def __init__(self, target_ratio: float = 1.0) -> None:
        self._target_ratio = target_ratio

    def evaluate(self, state, facts, coverage: CoverageRecord) -> OracleResult:
        if coverage.known and coverage.ratio >= self._target_ratio:
            return OracleResult(
                status="verified",
                evidence_refs=tuple(
                    sorted({ref for fact in facts for ref in fact.source_refs})
                ),
                reason=f"coverage {coverage.ratio:.2f}",
            )
        return OracleResult(status="inconclusive", reason="coverage_incomplete")


class VerifierTool(LoopToolPort):
    def __init__(self, replay_proofs: dict[str, str] | None = None) -> None:
        self._proofs = replay_proofs or {}
        self.executions: list[ActionProposal] = []

    def execute(self, proposal, *, idempotency_key) -> LoopToolResult:
        self.executions.append(proposal)
        if proposal.tool_ref != "evidence.replay":
            return LoopToolResult(status="denied", error="tool_not_allowed")
        candidate = proposal.input.get("candidate", "")
        proof = self._proofs.get(candidate)
        if proof is None:
            return LoopToolResult(
                status="completed",
                observations=[{"candidate": candidate, "replay": "no_proof"}],
            )
        return LoopToolResult(
            status="completed",
            observations=[{"candidate": candidate, "replay": "verified"}],
            facts=(
                FactRecord(
                    fact_id=f"fact_verified_{candidate}",
                    subject=candidate,
                    predicate="replay_proof",
                    value="verified",
                    source_refs=(proof,),
                    observed_at=utc_now(),
                ),
            ),
            evidence_refs=(proof,),
        )


class VerifierOracle(OraclePort):
    def evaluate(self, state, facts, coverage) -> OracleResult:
        proven = {
            fact.subject
            for fact in facts
            if fact.predicate == "replay_proof" and fact.value == "verified"
        }
        if proven and set(state.hypotheses).issubset(proven):
            return OracleResult(
                status="verified",
                evidence_refs=tuple(sorted({fact.source_refs[0] for fact in facts})),
                reason="all_candidates_replayed",
            )
        return OracleResult(status="not_verified", reason="missing_replay_proof")
