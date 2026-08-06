"""Relation-Bound Differential Adjudication (RBDA) core package.

Research milestone M1: turn an agent-reported finding + replayable trace into a
deterministic, relation-bound verdict (verified / rejected / unresolved) using
paired differential tests. No LLM is used inside the adjudication path.
"""

from .adjudicator import AdjudicationResult, AdjudicationVerdict, Adjudicator
from .effect_contract import EffectContract, HttpRequest
from .relation_mutation import (
    DifferentialOutcome,
    MutationKind,
    RelationContext,
    RelationMutation,
    RelationMutationGenerator,
)
from .separating_planner import Executor, SeparatingTestPlanner

__all__ = [
    "AdjudicationResult",
    "AdjudicationVerdict",
    "Adjudicator",
    "DifferentialOutcome",
    "EffectContract",
    "Executor",
    "HttpRequest",
    "MutationKind",
    "RelationContext",
    "RelationMutation",
    "RelationMutationGenerator",
    "SeparatingTestPlanner",
]
