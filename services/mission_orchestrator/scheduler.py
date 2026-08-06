from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from typing import Callable

from services.agent_runtime.kernel.contracts import LoopResult, LoopSpec, NodeSpec
from services.agent_runtime.kernel.loop import LoopRunner
from services.knowledge_service.models import FactRecord

from .contracts import (
    GraphMetrics,
    GraphNodeState,
    GraphPatch,
    GraphSnapshot,
    GraphState,
    HandoffPayload,
    handoff_fact,
)
from .blackboard import Blackboard
from .lease import LeaseRegistry
from .contracts import BackpressureError
from .graph_store import GraphStore


class GraphScheduler:
    def __init__(
        self,
        *,
        graph_id: str,
        mission_ref: str,
        nodes: dict[str, NodeSpec],
        edges: dict[str, tuple[str, ...]],
        blackboard: Blackboard,
        runner_factory: Callable[[LoopSpec], LoopRunner],
        target_ref: str,
        scope_hash: str = "scope_hash",
        max_retries: int = 2,
        leases: LeaseRegistry | None = None,
        default_lease_seconds: int = 120,
        max_pending_handoffs: int | None = None,
        store: GraphStore | None = None,
        loop_checkpoint_store=None,
    ) -> None:
        self._graph_id = graph_id
        self._mission_ref = mission_ref
        self._nodes = nodes
        self._edges = edges
        self._blackboard = blackboard
        self._runner_factory = runner_factory
        self._target_ref = target_ref
        self._scope_hash = scope_hash
        self._max_retries = max_retries
        self._leases = leases
        self._default_lease_seconds = default_lease_seconds
        self._max_pending_handoffs = max_pending_handoffs
        self._store = store
        self._loop_checkpoint_store = loop_checkpoint_store
        self._lock = threading.Lock()
        self._handoffs: list[HandoffPayload] = []
        self._dead_letters: list[str] = []
        self._pending_fanouts: dict[str, LoopResult] = {}
        self._duplicate_actions: list[str] = []
        self._seen_actions: set[str] = set()
        self._replan_count = 0
        self._executed_node_ids: set[str] = set()
        self._reached_edges: set[tuple[str, str]] = set()
        self._loop_events_by_node: dict[str, list] = {}

        restored = store.load(graph_id) if store is not None else None
        if restored is not None:
            self._nodes = restored["nodes"]
            self._edges = restored["edges"]
            self._handoffs = list(restored["handoffs"])
            for handoff in self._handoffs:
                self._blackboard.append(handoff_fact(handoff))
            recovered_states = restored["node_states"]
            # A durable workflow engine must reclaim tasks that were in
            # flight when the process died; otherwise the restored scheduler
            # sees "running" forever and the graph can never progress.
            for state in recovered_states.values():
                if state.status in (
                    "running",
                    "waiting",
                    "backpressure_waiting",
                    "lease_conflict",
                ):
                    state.status = "pending"
                    state.result = None
                    state.handoff_refs = ()
            for state in recovered_states.values():
                if state.result is None:
                    continue
                for fact in state.result.facts:
                    self._blackboard.append(fact)
            self._dead_letters = [
                node_id
                for node_id, state in recovered_states.items()
                if state.dead_letter
            ]
            self._replan_count = len(restored.get("patches") or ())
            self._state = GraphState(
                graph_id=graph_id,
                version=str(restored["version"]),
                mission_ref=mission_ref,
                node_states=recovered_states,
            )
            self._snapshots = {
                str(restored["version"]): GraphSnapshot(
                    graph_id=graph_id,
                    version=str(restored["version"]),
                    parent_version=None,
                    state=deepcopy(self._state),
                )
            }
            self._executed_node_ids = {
                node_id
                for node_id, state in self._state.node_states.items()
                if state.status in ("succeeded", "failed", "dead_letter")
            }
            self._reached_edges = {
                (handoff.from_node, handoff.to_node)
                for handoff in self._handoffs
            }
        else:
            self._state = GraphState(
                graph_id=graph_id,
                version="v1",
                mission_ref=mission_ref,
                node_states={
                    node_id: GraphNodeState(node_id=node_id)
                    for node_id in nodes
                },
            )
            self._snapshots = {
                "v1": GraphSnapshot(
                    graph_id=graph_id,
                    version="v1",
                    parent_version=None,
                    state=deepcopy(self._state),
                )
            }
            self._persist()

    @property
    def current_snapshot(self) -> GraphSnapshot:
        return GraphSnapshot(
            graph_id=self._graph_id,
            version=self._state.version,
            parent_version=None,
            state=deepcopy(self._state),
        )

    @property
    def state(self) -> GraphState:
        return self._state

    @property
    def handoffs(self) -> tuple[HandoffPayload, ...]:
        return tuple(self._handoffs)

    def snapshot(self, version: str) -> GraphSnapshot:
        return self._snapshots[version]

    def execute_node(self, node_id: str) -> GraphNodeState:
        node = self._nodes[node_id]
        state_entry = self._state.node_states[node_id]
        if node.node_type == "human":
            state_entry.status = "waiting_human"
            state_entry.human_payload = {
                "prompt": node.human_prompt or node_id,
                "node_id": node_id,
            }
            self._persist()
            return state_entry
        if not self._preconditions_met(node):
            state_entry.status = "waiting"
            return state_entry
        self._executed_node_ids.add(node_id)
        state_entry.status = "running"

        if node.loop_spec is not None:
            runner = self._runner_factory(node.loop_spec)
            known = tuple(node.loop_spec.budget.get("known_endpoints", ()))
            hypotheses = tuple(node.loop_spec.budget.get("hypotheses", ()))
            resumed = False
            if self._loop_checkpoint_store is not None:
                checkpoint_ref = f"graph:{self._graph_id}:{node_id}"
                runner.attach_checkpoint_store(
                    self._loop_checkpoint_store,
                    checkpoint_ref=checkpoint_ref,
                )
                checkpoint = self._loop_checkpoint_store.load(
                    checkpoint_ref
                )
                if checkpoint is not None:
                    runner.restore_checkpoint(checkpoint)
                    resumed = True
            self._loop_events_by_node[node_id] = list(runner.events)
            result = runner.run(
                known_endpoints=known,
                hypotheses=hypotheses,
                resumed=resumed,
            )
            state_entry.result = result
            for fact in result.facts:
                self._blackboard.append(fact)
            self._track_duplicate_actions(runner)
            if result.status == "succeeded":
                state_entry.status = "succeeded"
                self._fan_out(node, result)
            elif result.status in ("failed", "inconclusive"):
                state_entry.retries += 1
                if state_entry.retries > self._max_retries:
                    state_entry.status = "dead_letter"
                    state_entry.dead_letter = True
                    self._dead_letters.append(node_id)
                else:
                    state_entry.status = result.status
            else:
                state_entry.status = result.status
        elif node.node_type == "aggregate":
            merged = self._merge_handoffs(node)
            if merged is not None:
                self._blackboard.append(merged)
            state_entry.status = "succeeded"
        else:
            state_entry.status = "succeeded"
        self._persist()
        return state_entry

    def resolve_human(
        self,
        node_id: str,
        *,
        approved: bool,
        reason: str = "",
    ) -> GraphNodeState:
        state_entry = self._state.node_states[node_id]
        if state_entry.status != "waiting_human":
            raise ValueError(f"node {node_id} is not waiting for human input")
        state_entry.status = "succeeded" if approved else "failed"
        state_entry.human_payload = {
            **(state_entry.human_payload or {}),
            "approved": approved,
            "reason": reason,
            "resolved_at": _utc_now(),
        }
        if approved:
            self._executed_node_ids.add(node_id)
            self._fan_out(
                self._nodes[node_id],
                LoopResult(
                    status="succeeded",
                    stop_reason="human_approved",
                ),
            )
        self._persist()
        return state_entry

    def run_ready(
        self,
        *,
        worker_id: str = "worker_1",
    ) -> dict[str, GraphNodeState]:
        results: dict[str, GraphNodeState] = {}
        while True:
            requeued = False
            for node_id, state in self._state.node_states.items():
                if (
                    state.status in ("failed", "inconclusive")
                    and not state.dead_letter
                    and state.retries <= self._max_retries
                    and self._nodes[node_id].node_type != "human"
                ):
                    state.status = "pending"
                    requeued = True
            progressed = False
            for node_id in self._nodes:
                state = self._state.node_states[node_id]
                if state.status != "pending":
                    continue
                if not self._preconditions_met(self._nodes[node_id]):
                    continue
                if not self._fan_in_met(node_id):
                    continue
                results[node_id] = self.execute_node(node_id)
                progressed = True
            if not progressed and not requeued:
                break
        return results

    def execute_node_queued(self, node_id: str) -> GraphNodeState:
        try:
            return self.execute_node(node_id)
        except BackpressureError:
            state_entry = self._state.node_states[node_id]
            if state_entry.result is not None:
                self._pending_fanouts[node_id] = state_entry.result
            state_entry.status = "backpressure_waiting"
            return state_entry

    def drain_pending_fanouts(self) -> int:
        drained = 0
        for node_id in list(self._pending_fanouts):
            state_entry = self._state.node_states[node_id]
            result = self._pending_fanouts[node_id]
            if (
                self._max_pending_handoffs is not None
                and len(self._handoffs) >= self._max_pending_handoffs
            ):
                continue
            self._fan_out(self._nodes[node_id], result)
            state_entry.status = "succeeded"
            del self._pending_fanouts[node_id]
            drained += 1
        return drained

    def execute_group(
        self,
        node_ids: list[str],
        *,
        worker_id: str = "worker_1",
        parallel: bool = False,
    ) -> dict[str, GraphNodeState]:
        results: dict[str, GraphNodeState] = {}
        def run(node_id: str) -> tuple[str, GraphNodeState]:
            if self._leases is not None:
                lease = self._leases.acquire(
                    node_id,
                    worker_id,
                    self._default_lease_seconds,
                )
                if lease is None:
                    return node_id, GraphNodeState(
                        node_id=node_id,
                        status="lease_conflict",
                    )
            with self._lock:
                state = self.execute_node(node_id)
            if self._leases is not None:
                self._leases.release(node_id)
            return node_id, state

        if parallel:
            with ThreadPoolExecutor(max_workers=max(1, min(4, len(node_ids)))) as pool:
                futures = [pool.submit(run, node_id) for node_id in node_ids]
                for future in futures:
                    node_id, state = future.result()
                    results[node_id] = state
        else:
            for node_id in node_ids:
                node_id, state = run(node_id)
                results[node_id] = state
        return results

    def apply_patch(self, patch: GraphPatch) -> GraphSnapshot:
        if patch.parent_version != self._state.version:
            raise ValueError("patch parent version mismatch")
        if not patch.policy_checked:
            raise ValueError("patch was not re-checked against policy/scope")
        if patch.human_required:
            raise ValueError("patch requires a human gate")
        new_state = deepcopy(self._state)
        for op in patch.operations:
            if op.get("op") == "add_node":
                node: NodeSpec = op["node"]
                self._nodes[node.node_id] = node
                new_state.node_states[node.node_id] = GraphNodeState(node_id=node.node_id)
            elif op.get("op") == "add_edge":
                source = op["source"]
                target = op["target"]
                self._edges.setdefault(source, [])
                self._edges[source] = tuple(dict.fromkeys((*self._edges[source], target)))
                if source in self._executed_node_ids:
                    self._reached_edges.add((source, target))
            elif op.get("op") == "remove_edge":
                source = op["source"]
                target = op["target"]
                current = list(self._edges.get(source, ()))
                if target in current:
                    self._edges[source] = tuple(
                        item for item in current if item != target
                    )
                self._reached_edges.discard((source, target))
        new_version = f"v{len(self._snapshots) + 1}"
        new_state.version = new_version
        snapshot = GraphSnapshot(
            graph_id=self._graph_id,
            version=new_version,
            parent_version=patch.parent_version,
            state=new_state,
        )
        self._snapshots[new_version] = snapshot
        self._state = new_state
        self._replan_count += 1
        if self._store is not None:
            self._store.save_patch(self._graph_id, patch, new_version)
            self._persist()
        return snapshot

    def metrics(self) -> GraphMetrics:
        duplicate_actions = len(
            [action for action in self._duplicate_actions if action]
        )
        node_count = len(self._state.node_states)
        handoff_count = len(self._handoffs)
        path_efficiency = (
            round(handoff_count / node_count, 3) if node_count else 1.0
        )
        fanout_branches = sum(
            len(self._edges.get(node_id, ()))
            for node_id in self._executed_node_ids
            if self._state.node_states[node_id].status == "succeeded"
        )
        executed_targets = sum(
            1
            for source, targets in self._edges.items()
            for target in targets
            if source in self._executed_node_ids
            and target in self._executed_node_ids
        )
        branch_coverage = (
            round(executed_targets / max(1, fanout_branches), 3)
            if fanout_branches
            else 1.0
        )
        retried = [
            state
            for state in self._state.node_states.values()
            if state.retries > 0
        ]
        recovered = [
            state
            for state in retried
            if state.status == "succeeded"
        ]
        handoff_loss = sum(
            1
            for handoff in self._handoffs
            if handoff.to_node not in self._executed_node_ids
        )
        return GraphMetrics(
            handoffs=handoff_count,
            dead_letters=len(self._dead_letters),
            duplicate_actions=duplicate_actions,
            node_count=node_count,
            path_efficiency=path_efficiency,
            replans=self._replan_count,
            handoff_loss=handoff_loss,
            branch_coverage=branch_coverage,
            node_recovery_rate=(
                round(len(recovered) / max(1, len(retried)), 3)
                if retried
                else 1.0
            ),
            dead_letter_rate=(
                round(len(self._dead_letters) / max(1, node_count), 3)
            ),
            fanout_branches=fanout_branches,
        )

    def loop_events(
        self,
        node_id: str | None = None,
    ) -> dict[str, list] | list:
        if node_id is not None:
            return list(self._loop_events_by_node.get(node_id, ()))
        return dict(self._loop_events_by_node)

    def _fan_in_met(self, node_id: str) -> bool:
        incoming = [
            source
            for source, targets in self._edges.items()
            if node_id in targets
        ]
        return all(
            source in self._executed_node_ids
            and (source, node_id) in self._reached_edges
            for source in incoming
        )

    def _preconditions_met(self, node: NodeSpec) -> bool:
        facts = {view.fact.subject for view in self._blackboard.projection()}
        return all(condition in facts for condition in node.preconditions)

    def _fan_out(self, node: NodeSpec, result: LoopResult) -> None:
        conditions = node.edge_conditions or ("succeeded",)
        if result.status not in conditions:
            return
        if (
            self._max_pending_handoffs is not None
            and len(self._handoffs) >= self._max_pending_handoffs
        ):
            raise BackpressureError(
                f"pending handoff limit {self._max_pending_handoffs} reached"
            )
        fact_refs = tuple(fact.fact_id for fact in result.facts)
        for target in self._edges.get(node.node_id, ()):
            payload = HandoffPayload(
                from_node=node.node_id,
                to_node=target,
                fact_refs=fact_refs,
                evidence_refs=result.evidence_refs,
                summary=f"{node.node_id}:{result.status}",
            )
            self._handoffs.append(payload)
            self._reached_edges.add((node.node_id, target))
            self._state.node_states[node.node_id].handoff_refs += (payload.from_node,)
            self._blackboard.append(handoff_fact(payload))
        self._persist()

    def _merge_handoffs(self, node: NodeSpec) -> FactRecord | None:
        incoming = [payload for payload in self._handoffs if payload.to_node == node.node_id]
        if not incoming:
            return None
        subjects = tuple(sorted({f"handoff://{payload.from_node}" for payload in incoming}))
        return FactRecord(
            fact_id=f"fact_aggregate_{node.node_id}",
            subject="/".join(subjects),
            predicate="aggregated",
            value=",".join(subjects),
            source_refs=tuple(
                sorted({ref for payload in incoming for ref in payload.evidence_refs})
            ),
            confidence=1.0,
            trust="project_observed",
        )

    def _track_duplicate_actions(self, runner: LoopRunner) -> None:
        for event in runner.events:
            if event.event_type == "loop.action.proposed":
                key = (
                    f"{event.payload.get('tool')}:"
                    f"{json.dumps(event.payload.get('input') or {}, sort_keys=True)}"
                )
                if key in self._seen_actions:
                    self._duplicate_actions.append(key)
                self._seen_actions.add(key)

    def _persist(self) -> None:
        if self._store is None:
            return
        self._store.save_snapshot(
            self._graph_id,
            version=self._state.version,
            mission_ref=self._mission_ref,
            target_ref=self._target_ref,
            nodes=self._nodes,
            edges=self._edges,
            node_states=self._state.node_states,
            handoffs=list(self._handoffs),
            updated_at=_utc_now(),
        )


def _utc_now() -> str:
    from services.control_plane.app.contracts import utc_now

    return utc_now()
