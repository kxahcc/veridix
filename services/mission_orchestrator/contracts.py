from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.agent_runtime.kernel.contracts import LoopResult, LoopSpec, NodeSpec
from services.knowledge_service.models import FactRecord


@dataclass
class GraphNodeState:
    node_id: str
    status: str = "pending"
    result: LoopResult | None = None
    harness_ref: str | None = None
    handoff_refs: tuple[str, ...] = ()
    retries: int = 0
    dead_letter: bool = False
    human_payload: dict[str, Any] | None = None


@dataclass
class GraphState:
    graph_id: str
    version: str
    mission_ref: str
    node_states: dict[str, GraphNodeState]
    pending_edges: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphSnapshot:
    graph_id: str
    version: str
    parent_version: str | None
    state: GraphState


@dataclass(frozen=True)
class GraphPatch:
    patch_id: str
    parent_version: str
    author: str
    reason: str
    affected_nodes: tuple[str, ...]
    operations: tuple[dict[str, Any], ...]
    policy_checked: bool = False
    human_required: bool = False


@dataclass(frozen=True)
class HandoffPayload:
    from_node: str
    to_node: str
    fact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class GraphMetrics:
    handoffs: int
    dead_letters: int
    duplicate_actions: int
    node_count: int
    path_efficiency: float
    replans: int = 0
    handoff_loss: int = 0
    branch_coverage: float = 1.0
    node_recovery_rate: float = 1.0
    dead_letter_rate: float = 0.0
    fanout_branches: int = 0


class BackpressureError(RuntimeError):
    pass


def handoff_fact(payload: HandoffPayload) -> FactRecord:
    return FactRecord(
        fact_id=f"fact_handoff_{payload.from_node}_{payload.to_node}",
        subject=f"handoff://{payload.from_node}",
        predicate="handed_to",
        value=payload.to_node,
        source_refs=payload.evidence_refs,
        confidence=1.0,
        trust="project_observed",
    )
