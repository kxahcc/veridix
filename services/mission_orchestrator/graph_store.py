from __future__ import annotations

from services.control_plane.app.thread_safe_sqlite import SqliteResult

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from services.agent_runtime.kernel.contracts import (
    FactRecord,
    LoopResult,
    LoopSpec,
    NodeSpec,
)

from .contracts import GraphNodeState, GraphPatch, HandoffPayload


SCHEMA = """
CREATE TABLE IF NOT EXISTS mission_graphs (
    graph_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    mission_ref TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mission_graph_nodes (
    graph_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    loop_spec_json TEXT NOT NULL DEFAULT '{}',
    preconditions_json TEXT NOT NULL DEFAULT '[]',
    edge_conditions_json TEXT NOT NULL DEFAULT '[]',
    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
    harness_profile TEXT NOT NULL DEFAULT 'default',
    knowledge_view TEXT NOT NULL DEFAULT 'mission',
    sandbox_profile TEXT NOT NULL DEFAULT 'S2',
    oracle_ref TEXT,
    required_capability TEXT NOT NULL DEFAULT 'tool_calling',
    human_prompt TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (graph_id, node_id)
);
CREATE TABLE IF NOT EXISTS mission_graph_edges (
    graph_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    PRIMARY KEY (graph_id, source_id, target_id)
);
CREATE TABLE IF NOT EXISTS mission_graph_node_states (
    graph_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT,
    handoff_refs_json TEXT NOT NULL DEFAULT '[]',
    retries INTEGER NOT NULL DEFAULT 0,
    dead_letter INTEGER NOT NULL DEFAULT 0,
    human_payload_json TEXT,
    PRIMARY KEY (graph_id, node_id)
);
CREATE TABLE IF NOT EXISTS mission_graph_patches (
    graph_id TEXT NOT NULL,
    patch_id TEXT NOT NULL,
    parent_version TEXT NOT NULL,
    new_version TEXT NOT NULL,
    author TEXT NOT NULL,
    reason TEXT NOT NULL,
    operations_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY (graph_id, patch_id)
);
CREATE TABLE IF NOT EXISTS mission_graph_handoffs (
    graph_id TEXT NOT NULL,
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node TEXT NOT NULL,
    to_node TEXT NOT NULL,
    fact_refs_json TEXT NOT NULL DEFAULT '[]',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT ''
);
"""


class GraphStore:
    """SQLite durable store for mission graph structure, state and patches."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def _execute(self, sql: str, params=()):
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return SqliteResult(cursor.fetchall(), cursor.rowcount)

    def close(self) -> None:
        self._conn.close()

    def save_graph(
        self,
        graph_id: str,
        *,
        version: str,
        mission_ref: str,
        target_ref: str,
        nodes: dict[str, NodeSpec],
        edges: dict[str, tuple[str, ...]],
        updated_at: str,
    ) -> None:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO mission_graphs (
                    graph_id, version, mission_ref, target_ref, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(graph_id) DO UPDATE SET
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (graph_id, version, mission_ref, target_ref, updated_at),
            )
            for node in nodes.values():
                self._execute(
                    """
                    INSERT INTO mission_graph_nodes (
                        graph_id, node_id, node_type, loop_spec_json,
                        preconditions_json, edge_conditions_json,
                        allowed_tools_json, harness_profile, knowledge_view,
                        sandbox_profile, oracle_ref, required_capability,
                        human_prompt
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(graph_id, node_id) DO UPDATE SET
                        node_type = excluded.node_type,
                        loop_spec_json = excluded.loop_spec_json,
                        preconditions_json = excluded.preconditions_json,
                        edge_conditions_json = excluded.edge_conditions_json,
                        allowed_tools_json = excluded.allowed_tools_json,
                        harness_profile = excluded.harness_profile,
                        knowledge_view = excluded.knowledge_view,
                        sandbox_profile = excluded.sandbox_profile,
                        oracle_ref = excluded.oracle_ref,
                        required_capability = excluded.required_capability,
                        human_prompt = excluded.human_prompt
                    """,
                    (
                        graph_id,
                        node.node_id,
                        node.node_type,
                        _json(_loop_spec_dict(node.loop_spec)),
                        _json(list(node.preconditions)),
                        _json(list(node.edge_conditions)),
                        _json(list(node.allowed_tools)),
                        node.harness_profile,
                        node.knowledge_view,
                        node.sandbox_profile,
                        node.oracle_ref,
                        node.required_capability,
                        node.human_prompt,
                    ),
                )
            self._execute(
                "DELETE FROM mission_graph_edges WHERE graph_id = ?",
                (graph_id,),
            )
            for source, targets in edges.items():
                for target in targets:
                    self._execute(
                        """
                        INSERT INTO mission_graph_edges (
                            graph_id, source_id, target_id
                        ) VALUES (?, ?, ?)
                        """,
                        (graph_id, source, target),
                    )

    def save_snapshot(
        self,
        graph_id: str,
        *,
        version: str,
        mission_ref: str,
        target_ref: str,
        nodes: dict[str, NodeSpec],
        edges: dict[str, tuple[str, ...]],
        node_states: dict[str, GraphNodeState],
        handoffs: list[HandoffPayload],
        updated_at: str,
    ) -> None:
        """Persist graph structure, node states and handoffs atomically.

        A crash inside this method rolls the whole snapshot back, so a new
        scheduler instance never observes a half-written graph where, for
        example, a handoff exists but the source node is still running.
        """
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO mission_graphs (
                    graph_id, version, mission_ref, target_ref, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(graph_id) DO UPDATE SET
                    version = excluded.version,
                    mission_ref = excluded.mission_ref,
                    target_ref = excluded.target_ref,
                    updated_at = excluded.updated_at
                """,
                (graph_id, version, mission_ref, target_ref, updated_at),
            )
            for node in nodes.values():
                self._execute(
                    """
                    INSERT INTO mission_graph_nodes (
                        graph_id, node_id, node_type, loop_spec_json,
                        preconditions_json, edge_conditions_json,
                        allowed_tools_json, harness_profile, knowledge_view,
                        sandbox_profile, oracle_ref, required_capability,
                        human_prompt
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(graph_id, node_id) DO UPDATE SET
                        node_type = excluded.node_type,
                        loop_spec_json = excluded.loop_spec_json,
                        preconditions_json = excluded.preconditions_json,
                        edge_conditions_json = excluded.edge_conditions_json,
                        allowed_tools_json = excluded.allowed_tools_json,
                        harness_profile = excluded.harness_profile,
                        knowledge_view = excluded.knowledge_view,
                        sandbox_profile = excluded.sandbox_profile,
                        oracle_ref = excluded.oracle_ref,
                        required_capability = excluded.required_capability,
                        human_prompt = excluded.human_prompt
                    """,
                    (
                        graph_id,
                        node.node_id,
                        node.node_type,
                        _json(_loop_spec_dict(node.loop_spec)),
                        _json(list(node.preconditions)),
                        _json(list(node.edge_conditions)),
                        _json(list(node.allowed_tools)),
                        node.harness_profile,
                        node.knowledge_view,
                        node.sandbox_profile,
                        node.oracle_ref,
                        node.required_capability,
                        node.human_prompt,
                    ),
                )
            self._execute(
                "DELETE FROM mission_graph_edges WHERE graph_id = ?",
                (graph_id,),
            )
            for source, targets in edges.items():
                for target in targets:
                    self._execute(
                        """
                        INSERT INTO mission_graph_edges (
                            graph_id, source_id, target_id
                        ) VALUES (?, ?, ?)
                        """,
                        (graph_id, source, target),
                    )
            for node_id, state in node_states.items():
                self._execute(
                    """
                    INSERT INTO mission_graph_node_states (
                        graph_id, node_id, status, result_json,
                        handoff_refs_json, retries, dead_letter,
                        human_payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(graph_id, node_id) DO UPDATE SET
                        status = excluded.status,
                        result_json = excluded.result_json,
                        handoff_refs_json = excluded.handoff_refs_json,
                        retries = excluded.retries,
                        dead_letter = excluded.dead_letter,
                        human_payload_json = excluded.human_payload_json
                    """,
                    (
                        graph_id,
                        node_id,
                        state.status,
                        _json(_loop_result_dict(state.result)),
                        _json(list(state.handoff_refs)),
                        state.retries,
                        int(state.dead_letter),
                        _json(state.human_payload),
                    ),
                )
            self._execute(
                "DELETE FROM mission_graph_handoffs WHERE graph_id = ?",
                (graph_id,),
            )
            for handoff in handoffs:
                self._execute(
                    """
                    INSERT INTO mission_graph_handoffs (
                        graph_id, from_node, to_node, fact_refs_json,
                        evidence_refs_json, summary
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        graph_id,
                        handoff.from_node,
                        handoff.to_node,
                        _json(list(handoff.fact_refs)),
                        _json(list(handoff.evidence_refs)),
                        handoff.summary,
                    ),
                )

    def save_node_state(
        self,
        graph_id: str,
        node_id: str,
        state: GraphNodeState,
    ) -> None:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO mission_graph_node_states (
                    graph_id, node_id, status, result_json, handoff_refs_json,
                    retries, dead_letter, human_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(graph_id, node_id) DO UPDATE SET
                    status = excluded.status,
                    result_json = excluded.result_json,
                    handoff_refs_json = excluded.handoff_refs_json,
                    retries = excluded.retries,
                    dead_letter = excluded.dead_letter,
                    human_payload_json = excluded.human_payload_json
                """,
                (
                    graph_id,
                    node_id,
                    state.status,
                    _json(_loop_result_dict(state.result)),
                    _json(list(state.handoff_refs)),
                    state.retries,
                    int(state.dead_letter),
                    _json(state.human_payload),
                ),
            )

    def save_handoff(
        self,
        graph_id: str,
        handoff: HandoffPayload,
    ) -> None:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO mission_graph_handoffs (
                    graph_id, from_node, to_node, fact_refs_json,
                    evidence_refs_json, summary
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    graph_id,
                    handoff.from_node,
                    handoff.to_node,
                    _json(list(handoff.fact_refs)),
                    _json(list(handoff.evidence_refs)),
                    handoff.summary,
                ),
            )

    def save_patch(
        self,
        graph_id: str,
        patch: GraphPatch,
        new_version: str,
    ) -> None:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO mission_graph_patches (
                    graph_id, patch_id, parent_version, new_version, author,
                    reason, operations_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    graph_id,
                    patch.patch_id,
                    patch.parent_version,
                    new_version,
                    patch.author,
                    patch.reason,
                    _json(list(patch.operations)),
                    _now(),
                ),
            )

    def load(self, graph_id: str) -> dict[str, Any] | None:
        graph_row = self._execute(
            "SELECT * FROM mission_graphs WHERE graph_id = ?",
            (graph_id,),
        ).fetchone()
        if graph_row is None:
            return None
        node_rows = self._execute(
            "SELECT * FROM mission_graph_nodes WHERE graph_id = ?",
            (graph_id,),
        ).fetchall()
        nodes: dict[str, NodeSpec] = {}
        for row in node_rows:
            nodes[row["node_id"]] = _node_spec_from_row(row)
        edge_rows = self._execute(
            """
            SELECT source_id, target_id FROM mission_graph_edges
            WHERE graph_id = ? ORDER BY source_id, target_id
            """,
            (graph_id,),
        ).fetchall()
        edges: dict[str, list[str]] = {}
        for row in edge_rows:
            edges.setdefault(row["source_id"], []).append(row["target_id"])
        state_rows = self._execute(
            "SELECT * FROM mission_graph_node_states WHERE graph_id = ?",
            (graph_id,),
        ).fetchall()
        node_states: dict[str, GraphNodeState] = {}
        for row in state_rows:
            node_states[row["node_id"]] = _node_state_from_row(row)
        handoff_rows = self._execute(
            """
            SELECT * FROM mission_graph_handoffs
            WHERE graph_id = ? ORDER BY seq
            """,
            (graph_id,),
        ).fetchall()
        handoffs = [_handoff_from_row(row) for row in handoff_rows]
        patch_rows = self._execute(
            """
            SELECT * FROM mission_graph_patches
            WHERE graph_id = ? ORDER BY created_at
            """,
            (graph_id,),
        ).fetchall()
        patches = [
            {
                "patch_id": row["patch_id"],
                "parent_version": row["parent_version"],
                "new_version": row["new_version"],
                "author": row["author"],
                "reason": row["reason"],
                "operations": _loads(row["operations_json"]),
            }
            for row in patch_rows
        ]
        return {
            "version": graph_row["version"],
            "mission_ref": graph_row["mission_ref"],
            "target_ref": graph_row["target_ref"],
            "nodes": nodes,
            "edges": {key: tuple(value) for key, value in edges.items()},
            "node_states": node_states,
            "handoffs": handoffs,
            "patches": patches,
        }

    def graph_count(self) -> int:
        row = self._execute(
            "SELECT COUNT(*) AS count FROM mission_graphs"
        ).fetchone()
        return int(row["count"])


def _loop_spec_dict(spec: LoopSpec | None) -> dict[str, Any]:
    if spec is None:
        return {}
    return {
        "loop_id": spec.loop_id,
        "profile": spec.profile,
        "version": spec.version,
        "max_iterations": spec.max_iterations,
        "allowed_tools": list(spec.allowed_tools),
        "stop_on_coverage": spec.stop_on_coverage,
        "budget": dict(spec.budget),
        "inputs": list(spec.inputs),
        "state_schema": spec.state_schema,
        "context_policy": spec.context_policy,
        "allowed_skills": list(spec.allowed_skills),
        "knowledge_query": list(spec.knowledge_query),
        "oracle": spec.oracle,
        "success_criteria": spec.success_criteria,
        "failure_policy": spec.failure_policy,
        "retry_policy": spec.retry_policy,
        "risk_level": spec.risk_level,
        "evidence_requirements": list(spec.evidence_requirements),
        "sandbox_profile": spec.sandbox_profile,
    }


def _loop_result_dict(result: LoopResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "status": result.status,
        "facts": [dict(fact.__dict__) for fact in result.facts],
        "evidence_refs": list(result.evidence_refs),
        "candidate_findings": list(result.candidate_findings),
        "stop_reason": result.stop_reason,
    }


def _node_spec_from_row(row: sqlite3.Row) -> NodeSpec:
    loop_data = _loads(row["loop_spec_json"]) or {}
    return NodeSpec(
        node_id=row["node_id"],
        node_type=row["node_type"],
        loop_spec=(
            LoopSpec(
                loop_id=loop_data["loop_id"],
                profile=loop_data["profile"],
                version=str(loop_data.get("version") or "1.0"),
                max_iterations=int(loop_data.get("max_iterations", 10)),
                allowed_tools=tuple(loop_data.get("allowed_tools", ())),
                stop_on_coverage=float(
                    loop_data.get("stop_on_coverage", 1.0)
                ),
                budget=dict(loop_data.get("budget", {})),
                inputs=tuple(loop_data.get("inputs", ())),
                state_schema=str(loop_data.get("state_schema") or ""),
                context_policy=str(loop_data.get("context_policy") or ""),
                allowed_skills=tuple(loop_data.get("allowed_skills", ())),
                knowledge_query=tuple(
                    loop_data.get("knowledge_query", ())
                ),
                oracle=str(loop_data.get("oracle") or ""),
                success_criteria=str(loop_data.get("success_criteria") or ""),
                failure_policy=str(loop_data.get("failure_policy") or ""),
                retry_policy=str(loop_data.get("retry_policy") or ""),
                risk_level=str(loop_data.get("risk_level") or ""),
                evidence_requirements=tuple(
                    loop_data.get("evidence_requirements", ())
                ),
                sandbox_profile=str(loop_data.get("sandbox_profile") or ""),
            )
            if loop_data
            else None
        ),
        preconditions=tuple(_loads(row["preconditions_json"]) or ()),
        edge_conditions=tuple(_loads(row["edge_conditions_json"]) or ()),
        allowed_tools=tuple(_loads(row["allowed_tools_json"]) or ()),
        harness_profile=row["harness_profile"],
        knowledge_view=row["knowledge_view"],
        sandbox_profile=row["sandbox_profile"],
        oracle_ref=row["oracle_ref"],
        required_capability=row["required_capability"],
        human_prompt=row["human_prompt"],
    )


def _node_state_from_row(row: sqlite3.Row) -> GraphNodeState:
    result_data = _loads(row["result_json"]) if row["result_json"] else None
    return GraphNodeState(
        node_id=row["node_id"],
        status=row["status"],
        result=_loop_result_from_dict(result_data) if result_data else None,
        handoff_refs=tuple(_loads(row["handoff_refs_json"]) or ()),
        retries=int(row["retries"]),
        dead_letter=bool(row["dead_letter"]),
        human_payload=(
            _loads(row["human_payload_json"])
            if row["human_payload_json"]
            else None
        ),
    )


def _loop_result_from_dict(data: dict[str, Any]) -> LoopResult:
    return LoopResult(
        status=data["status"],
        facts=tuple(
            FactRecord(**fact)
            for fact in data.get("facts", [])
        ),
        evidence_refs=tuple(data.get("evidence_refs", ())),
        candidate_findings=tuple(data.get("candidate_findings", ())),
        stop_reason=data.get("stop_reason"),
    )


def _handoff_from_row(row: sqlite3.Row) -> HandoffPayload:
    return HandoffPayload(
        from_node=row["from_node"],
        to_node=row["to_node"],
        fact_refs=tuple(_loads(row["fact_refs_json"]) or ()),
        evidence_refs=tuple(_loads(row["evidence_refs_json"]) or ()),
        summary=row["summary"],
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, default=str)


def _loads(value: str) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _now() -> str:
    from services.control_plane.app.contracts import utc_now

    return utc_now()
