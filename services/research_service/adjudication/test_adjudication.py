from __future__ import annotations

from typing import Any

from services.research_service.adjudication.adjudicator import (
    AdjudicationVerdict,
    Adjudicator,
)
from services.research_service.adjudication.effect_contract import (
    EffectContract,
    HttpRequest,
)
from services.research_service.adjudication.relation_mutation import (
    MutationKind,
    RelationContext,
    RelationMutation,
    RelationMutationGenerator,
)
from services.research_service.adjudication.separating_planner import (
    SeparatingTestPlanner,
)


def make_contract(claim_id: str = "claim_1") -> EffectContract:
    return EffectContract(
        claim_id=claim_id,
        vuln_category="IDOR",
        target_ref="https://lab.example.test",
        principal="attacker",
        victim_principal="victim",
        resource_ref="resource-42",
        session_ref="session-attacker",
        state_epoch="epoch-1",
        action_sequence=(
            HttpRequest(
                method="POST",
                path="/api/login",
                headers={"authorization": "principal:attacker"},
                body='{"user":"attacker"}',
                label="login",
            ),
            HttpRequest(
                method="GET",
                path="/api/resource/resource-42",
                headers={"authorization": "principal:attacker"},
                label="read",
            ),
        ),
        claimed_effect="unauthorized_read",
        observed_signal="response contains victim resource body",
    )


class FakeExecutor:
    """Deterministic executor: effect_detected is read from a mapping."""

    def __init__(self, effects: dict[str, bool]) -> None:
        self._effects = effects

    def execute(self, mutation: RelationMutation) -> list[dict[str, Any]]:
        detected = self._effects.get(mutation.mutation_id, False)
        return [
            {
                "effect_detected": detected,
                "signal": f"{mutation.kind.value}:{'hit' if detected else 'clean'}",
            }
            for _ in mutation.requests
        ]


def generate_mutations() -> list[RelationMutation]:
    context = RelationContext(
        alternate_principals=("attacker-alt",),
        alternate_resources=("resource-99",),
        alternate_targets=("mirror.lab.example.test",),
    )
    return RelationMutationGenerator(context).generate(make_contract())


def mutation_map(
    mutations: list[RelationMutation],
) -> dict[MutationKind, RelationMutation]:
    return {m.kind: m for m in mutations}


def test_generator_covers_all_relation_dimensions() -> None:
    mutations = generate_mutations()
    kinds = {m.kind for m in mutations}
    assert MutationKind.BASELINE in kinds
    assert MutationKind.PRINCIPAL_SWAP in kinds
    assert MutationKind.RESOURCE_SWAP in kinds
    assert MutationKind.SESSION_RESET in kinds
    assert MutationKind.STATE_RESET in kinds
    assert MutationKind.ORDER_CHANGE in kinds
    assert MutationKind.TARGET_IDENTITY in kinds
    assert MutationKind.BENIGN_CONTROL in kinds
    # Every mutation has a concrete replayable plan.
    assert all(m.requests for m in mutations)
    assert all(m.cost == len(m.requests) for m in mutations)


def test_contract_roundtrip() -> None:
    contract = make_contract()
    assert EffectContract.from_dict(contract.to_dict()) == contract


def test_verified_when_binding_established() -> None:
    mutations = generate_mutations()
    by_kind = mutation_map(mutations)
    effects = {
        by_kind[MutationKind.BASELINE].mutation_id: True,
        by_kind[MutationKind.PRINCIPAL_SWAP].mutation_id: False,
        by_kind[MutationKind.BENIGN_CONTROL].mutation_id: False,
    }
    result = Adjudicator(budget=10).adjudicate(
        make_contract(),
        mutations,
        FakeExecutor(effects),
    )
    assert result.verdict is AdjudicationVerdict.VERIFIED
    assert result.reason == "binding_established"
    assert result.positive_outcome is True
    # Planner does not stop early on verified: it conservatively runs all
    # controls the budget allows and the adjudicator decides afterwards.
    assert result.converged is False


def test_rejected_when_baseline_not_reproduced() -> None:
    mutations = generate_mutations()
    by_kind = mutation_map(mutations)
    effects = {by_kind[MutationKind.BASELINE].mutation_id: False}
    result = Adjudicator(budget=10).adjudicate(
        make_contract(),
        mutations,
        FakeExecutor(effects),
    )
    assert result.verdict is AdjudicationVerdict.REJECTED
    assert result.reason == "baseline_not_reproduced"


def test_rejected_when_benign_control_confounded() -> None:
    mutations = generate_mutations()
    by_kind = mutation_map(mutations)
    effects = {
        by_kind[MutationKind.BASELINE].mutation_id: True,
        by_kind[MutationKind.BENIGN_CONTROL].mutation_id: True,
    }
    result = Adjudicator(budget=10).adjudicate(
        make_contract(),
        mutations,
        FakeExecutor(effects),
    )
    assert result.verdict is AdjudicationVerdict.REJECTED
    assert result.reason == "benign_control_confounded"


def test_rejected_when_binding_negative_still_shows_effect() -> None:
    mutations = generate_mutations()
    by_kind = mutation_map(mutations)
    effects = {
        by_kind[MutationKind.BASELINE].mutation_id: True,
        # principal swap still reads the resource -> binding not established
        by_kind[MutationKind.PRINCIPAL_SWAP].mutation_id: True,
    }
    result = Adjudicator(budget=10).adjudicate(
        make_contract(),
        mutations,
        FakeExecutor(effects),
    )
    assert result.verdict is AdjudicationVerdict.REJECTED
    assert result.reason == "binding_negative_confounded"


def test_unresolved_when_no_discriminating_controls_within_budget() -> None:
    mutations = generate_mutations()
    by_kind = mutation_map(mutations)
    effects = {
        by_kind[MutationKind.BASELINE].mutation_id: True,
    }
    # Budget covers only the baseline (cost 2): no control executed, so the
    # signal cannot be proven discriminative -> unresolved.
    result = Adjudicator(budget=2).adjudicate(
        make_contract(),
        mutations,
        FakeExecutor(effects),
    )
    assert result.verdict is AdjudicationVerdict.UNRESOLVED
    assert result.reason == "budget_exhausted"
    assert result.tests_executed == 1
    assert result.budget_spent == 2


def test_planner_respects_budget_and_prefers_cheap_controls() -> None:
    mutations = generate_mutations()
    planner = SeparatingTestPlanner()
    by_kind = mutation_map(mutations)
    plan = planner.plan(
        make_contract(),
        mutations,
        budget=4,
        executor=FakeExecutor({by_kind[MutationKind.BASELINE].mutation_id: True}),
    )
    assert plan.budget_spent <= 4
    # Baseline has cost 2; the cheapest control (state_reset, cost 1) fits,
    # the next cheapest (cost 2) does not -> exactly 2 tests.
    assert plan.tests_executed == 2
    assert plan.outcomes[0].kind is MutationKind.BASELINE


def test_planner_converges_when_claim_clearly_refuted() -> None:
    mutations = generate_mutations()
    by_kind = mutation_map(mutations)
    effects = {
        by_kind[MutationKind.BASELINE].mutation_id: True,
        # state_reset is the cheapest control, so the refutation fires early.
        by_kind[MutationKind.STATE_RESET].mutation_id: True,
    }
    plan = SeparatingTestPlanner().plan(
        make_contract(),
        mutations,
        budget=10,
        executor=FakeExecutor(effects),
    )
    assert plan.converged is True
    assert plan.outcomes[-1].effect_detected is True
