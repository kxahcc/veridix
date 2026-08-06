from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .effect_contract import EffectContract
from .relation_mutation import DifferentialOutcome, MutationKind, RelationMutation


class Executor(Protocol):
    """Executes a differential test against the authorized lab target.

    Returns one observation dict per executed request. Each observation may
    carry ``effect_detected``; the planner's default check accepts any of them.
    """

    def execute(self, mutation: RelationMutation) -> list[dict[str, Any]]: ...


EffectCheck = Callable[[list[dict[str, Any]]], bool]


def _default_effect_check(observations: list[dict[str, Any]]) -> bool:
    return any(bool(obs.get("effect_detected")) for obs in observations)


@dataclass(frozen=True)
class PlanResult:
    outcomes: tuple[DifferentialOutcome, ...]
    converged: bool
    tests_executed: int
    budget_spent: int


class SeparatingTestPlanner:
    """Greedy minimal separating-test planner.

    Executes the baseline first, then prefers cheap, highly discriminating
    controls. Stops as soon as the outcome set is decisive (a claim is
    confirmed or refuted) or the budget is exhausted.
    """

    def __init__(self, effect_check: EffectCheck | None = None) -> None:
        self._effect_check = effect_check or _default_effect_check

    def plan(
        self,
        contract: EffectContract,
        mutations: list[RelationMutation],
        *,
        budget: int,
        executor: Executor,
    ) -> PlanResult:
        ordered = self._order(contract, mutations)
        outcomes: list[DifferentialOutcome] = []
        budget_spent = 0
        converged = False
        for mutation in ordered:
            if budget_spent + mutation.cost > budget:
                continue
            observations = executor.execute(mutation)
            budget_spent += mutation.cost
            detected = self._effect_check(observations)
            outcomes.append(
                DifferentialOutcome(
                    mutation_id=mutation.mutation_id,
                    kind=mutation.kind,
                    expected_direction=mutation.expected_direction,
                    effect_detected=detected,
                    signal=_first_signal(observations),
                    observations=tuple(observations),
                )
            )
            if self._is_decisive(mutation, detected):
                converged = True
                break
        return PlanResult(
            outcomes=tuple(outcomes),
            converged=converged,
            tests_executed=len(outcomes),
            budget_spent=budget_spent,
        )

    def _order(
        self,
        contract: EffectContract,
        mutations: list[RelationMutation],
    ) -> list[RelationMutation]:
        baseline = [m for m in mutations if m.kind is MutationKind.BASELINE]
        rest = [m for m in mutations if m.kind is not MutationKind.BASELINE]
        rest.sort(
            key=lambda m: (
                # Cheap and strongly discriminating controls go first.
                m.cost,
                0 if m.expected_direction != "effect_persists" else 1,
                m.kind.value,
            )
        )
        return baseline + rest

    def _is_decisive(
        self,
        mutation: RelationMutation,
        detected: bool,
    ) -> bool:
        if mutation.expected_direction == "effect_persists":
            return not detected
        return detected


def _first_signal(observations: list[dict[str, Any]]) -> str:
    for obs in observations:
        signal = obs.get("signal")
        if signal:
            return str(signal)
    return ""
