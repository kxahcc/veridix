from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .effect_contract import EffectContract
from .relation_mutation import (
    DifferentialOutcome,
    MutationKind,
    RelationMutation,
)
from .separating_planner import Executor, PlanResult, SeparatingTestPlanner


class AdjudicationVerdict(str, Enum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class AdjudicationResult:
    verdict: AdjudicationVerdict
    reason: str
    positive_outcome: bool
    outcomes: tuple[DifferentialOutcome, ...]
    tests_executed: int
    budget_spent: int
    converged: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "positive_outcome": self.positive_outcome,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "tests_executed": self.tests_executed,
            "budget_spent": self.budget_spent,
            "converged": self.converged,
        }


class Adjudicator:
    """Deterministic, LLM-free adjudicator over differential outcomes.

    Rules:
      - baseline must reproduce the claimed effect, otherwise rejected;
      - benign control must not show the effect, otherwise the observed
        signal is confounded and the claim is rejected;
      - every binding-negative control must not show the effect, otherwise
        the binding is not established and the claim is rejected;
      - at least one binding-negative control must cleanly vanish the effect
        for the claim to be verified;
      - otherwise the claim is unresolved (kept conservative).
    """

    def __init__(
        self,
        planner: SeparatingTestPlanner | None = None,
        *,
        budget: int = 10,
    ) -> None:
        self._planner = planner or SeparatingTestPlanner()
        self._budget = budget

    def adjudicate(
        self,
        contract: EffectContract,
        mutations: list[RelationMutation],
        executor: Executor,
    ) -> AdjudicationResult:
        result = self._planner.plan(
            contract,
            mutations,
            budget=self._budget,
            executor=executor,
        )
        return self._decide(result)

    def _decide(self, result: PlanResult) -> AdjudicationResult:
        outcomes = result.outcomes
        baseline = next(
            (o for o in outcomes if o.kind is MutationKind.BASELINE), None
        )
        positive = bool(baseline and baseline.effect_detected)

        if baseline is None:
            return self._result(
                AdjudicationVerdict.UNRESOLVED,
                "no_baseline_executed",
                positive,
                result,
            )
        if not positive:
            return self._result(
                AdjudicationVerdict.REJECTED,
                "baseline_not_reproduced",
                positive,
                result,
            )

        benign = [
            o for o in outcomes if o.kind is MutationKind.BENIGN_CONTROL
        ]
        if any(o.effect_detected for o in benign):
            return self._result(
                AdjudicationVerdict.REJECTED,
                "benign_control_confounded",
                positive,
                result,
            )

        negatives = [
            o
            for o in outcomes
            if o.kind is not MutationKind.BASELINE
            and o.kind is not MutationKind.BENIGN_CONTROL
        ]
        if any(o.effect_detected for o in negatives):
            return self._result(
                AdjudicationVerdict.REJECTED,
                "binding_negative_confounded",
                positive,
                result,
            )

        benign_clean = any(not o.effect_detected for o in benign)
        vanish_clean = any(
            o.expected_direction == "effect_vanishes" and not o.effect_detected
            for o in negatives
        )
        if benign_clean and vanish_clean:
            return self._result(
                AdjudicationVerdict.VERIFIED,
                "binding_established",
                positive,
                result,
            )

        if result.converged:
            return self._result(
                AdjudicationVerdict.UNRESOLVED,
                "no_discriminating_controls",
                positive,
                result,
            )
        return self._result(
            AdjudicationVerdict.UNRESOLVED,
            "budget_exhausted",
            positive,
            result,
        )

    def _result(
        self,
        verdict: AdjudicationVerdict,
        reason: str,
        positive: bool,
        plan: PlanResult,
    ) -> AdjudicationResult:
        return AdjudicationResult(
            verdict=verdict,
            reason=reason,
            positive_outcome=positive,
            outcomes=plan.outcomes,
            tests_executed=plan.tests_executed,
            budget_spent=plan.budget_spent,
            converged=plan.converged,
        )
