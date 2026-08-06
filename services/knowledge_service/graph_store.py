from __future__ import annotations

from services.control_plane.app.thread_safe_sqlite import SqliteResult

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import KnowledgeChunk


SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_nodes (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT 'entity',
    label TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS kg_edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (source_id, target_id, predicate)
);
CREATE TABLE IF NOT EXISTS kg_chunk_links (
    chunk_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (chunk_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_chunk_links_node
    ON kg_chunk_links(node_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_source
    ON kg_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_target
    ON kg_edges(target_id);
"""


class KnowledgeGraphStore:
    """SQLite knowledge graph for multi-channel RAG.

    Nodes represent entities, techniques, CWEs or attack patterns; edges
    carry semantic relations; chunk links bind knowledge chunks to the
    graph so retrieval can expand a lexical/vector hit into its neighbors.
    """

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

    def upsert_node(
        self,
        node_id: str,
        *,
        node_type: str,
        label: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO kg_nodes (node_id, node_type, label, properties)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    node_type = excluded.node_type,
                    label = excluded.label,
                    properties = excluded.properties
                """,
                (
                    node_id,
                    node_type,
                    label,
                    json.dumps(properties or {}, ensure_ascii=True),
                ),
            )

    def upsert_edge(
        self,
        source_id: str,
        target_id: str,
        predicate: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO kg_edges (
                    source_id, target_id, predicate, properties
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id, target_id, predicate) DO UPDATE SET
                    properties = excluded.properties
                """,
                (
                    source_id,
                    target_id,
                    predicate,
                    json.dumps(properties or {}, ensure_ascii=True),
                ),
            )

    def link_chunk(
        self,
        chunk_id: str,
        node_id: str,
        weight: float = 1.0,
    ) -> None:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT OR REPLACE INTO kg_chunk_links (
                    chunk_id, node_id, weight
                ) VALUES (?, ?, ?)
                """,
                (chunk_id, node_id, weight),
            )

    def register_chunk_graph(self, chunk: KnowledgeChunk) -> None:
        graph = chunk.graph or {}
        for node in graph.get("nodes", []):
            self.upsert_node(
                str(node["id"]),
                node_type=str(node.get("type", "entity")),
                label=str(node.get("label", node["id"])),
                properties=dict(node.get("properties") or {}),
            )
            self.link_chunk(chunk.chunk_id, str(node["id"]))
        for edge in graph.get("edges", []):
            self.upsert_edge(
                str(edge["source"]),
                str(edge["target"]),
                str(edge.get("predicate", "related_to")),
                properties=dict(edge.get("properties") or {}),
            )

    def nodes_for_terms(
        self,
        terms: tuple[str, ...],
        *,
        limit: int = 20,
    ) -> list[str]:
        if not terms:
            return []
        clauses: list[str] = []
        params: list[str] = []
        for term in terms:
            pattern = f"%{_escape_like(term)}%"
            clauses.append("label LIKE ?")
            clauses.append("node_id LIKE ?")
            params.extend([pattern, pattern])
        rows = self._execute(
            f"""
            SELECT node_id FROM kg_nodes
            WHERE {' OR '.join(clauses)}
            LIMIT {int(limit)}
            """,
            params,
        ).fetchall()
        return [row["node_id"] for row in rows]

    def neighbors(
        self,
        node_ids: tuple[str, ...],
        *,
        depth: int = 1,
        limit: int = 20,
    ) -> list[str]:
        if not node_ids:
            return []
        current = set(node_ids)
        seen = set(node_ids)
        for _ in range(max(1, depth)):
            if not current:
                break
            placeholders = ",".join("?" for _ in current)
            rows = self._execute(
                f"""
                SELECT source_id AS peer FROM kg_edges
                WHERE target_id IN ({placeholders})
                UNION
                SELECT target_id AS peer FROM kg_edges
                WHERE source_id IN ({placeholders})
                """,
                (*current, *current),
            ).fetchall()
            current = {row["peer"] for row in rows} - seen
            seen.update(current)
            if len(seen) >= limit:
                break
        return list(seen)

    def chunk_ids_for_nodes(
        self,
        node_ids: tuple[str, ...],
    ) -> dict[str, list[str]]:
        if not node_ids:
            return {}
        placeholders = ",".join("?" for _ in node_ids)
        rows = self._execute(
            f"""
            SELECT node_id, chunk_id FROM kg_chunk_links
            WHERE node_id IN ({placeholders})
            """,
            node_ids,
        ).fetchall()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row["node_id"], []).append(row["chunk_id"])
        return result

    def node_count(self) -> int:
        row = self._execute(
            "SELECT COUNT(*) AS count FROM kg_nodes"
        ).fetchone()
        return int(row["count"])

    def chunk_count(self) -> int:
        row = self._execute(
            "SELECT COUNT(DISTINCT chunk_id) AS count FROM kg_chunk_links"
        ).fetchone()
        return int(row["count"])

    def edge_count(self) -> int:
        row = self._execute(
            "SELECT COUNT(*) AS count FROM kg_edges"
        ).fetchone()
        return int(row["count"])

    def path_labels(
        self,
        node_ids: tuple[str, ...],
    ) -> dict[str, str]:
        if not node_ids:
            return {}
        placeholders = ",".join("?" for _ in node_ids)
        rows = self._execute(
            f"""
            SELECT node_id, label FROM kg_nodes
            WHERE node_id IN ({placeholders})
            """,
            node_ids,
        ).fetchall()
        return {row["node_id"]: row["label"] for row in rows}

    def snapshot(
        self,
        *,
        node_limit: int = 200,
        edge_limit: int = 600,
    ) -> dict:
        """Return a bounded, UI-friendly graph snapshot for visualization."""
        nodes = self._execute(
            """
            SELECT node_id, node_type, label, properties
            FROM kg_nodes
            ORDER BY node_id
            LIMIT ?
            """,
            (int(node_limit),),
        ).fetchall()
        node_ids = [row["node_id"] for row in nodes]
        edges: list[dict] = []
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            rows = self._execute(
                f"""
                SELECT source_id, target_id, predicate, properties
                FROM kg_edges
                WHERE source_id IN ({placeholders})
                   OR target_id IN ({placeholders})
                ORDER BY source_id, target_id, predicate
                LIMIT ?
                """,
                (*node_ids, *node_ids, int(edge_limit)),
            ).fetchall()
            edges = [
                {
                    "source": row["source_id"],
                    "target": row["target_id"],
                    "predicate": row["predicate"],
                }
                for row in rows
            ]
        chunk_links = self._execute(
            "SELECT node_id, COUNT(*) AS chunk_count FROM kg_chunk_links GROUP BY node_id"
        ).fetchall()
        chunk_count_by_node = {
            row["node_id"]: int(row["chunk_count"]) for row in chunk_links
        }
        return {
            "nodes": [
                {
                    "id": row["node_id"],
                    "label": row["label"],
                    "type": row["node_type"],
                    "chunks": chunk_count_by_node.get(row["node_id"], 0),
                }
                for row in nodes
            ],
            "edges": edges,
            "counts": {
                "nodes": self.node_count(),
                "edges": self.edge_count(),
                "chunks": self.chunk_count(),
            },
        }


def _escape_like(term: str) -> str:
    return re.sub(r"[%_]", lambda m: f"\\{m.group(0)}", term)
