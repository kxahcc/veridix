from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .models import NodeRegistration, NodeResult, RemoteNode, TaskLease

SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_nodes (
    node_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    public_key TEXT NOT NULL,
    status TEXT NOT NULL,
    last_seen_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_leases (
    lease_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    task_ref TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_results (
    result_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    task_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    artifact_refs TEXT NOT NULL,
    signature TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_connections (
    node_id TEXT PRIMARY KEY,
    connected_at TEXT NOT NULL,
    lease_until TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_payloads (
    task_ref TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class RemoteNodeRegistry:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def register(self, registration: NodeRegistration) -> RemoteNode:
        node = RemoteNode(
            node_id=registration.node_id,
            version=registration.version,
            capabilities=registration.capabilities,
            public_key=registration.public_key,
        )
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO remote_nodes
                    (node_id, version, capabilities, public_key, status,
                     last_seen_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    node.version,
                    json.dumps(node.capabilities),
                    node.public_key,
                    node.status,
                    node.last_seen_at,
                    node.created_at,
                ),
            )
        return node

    def heartbeat(self, node_id: str) -> RemoteNode:
        now = utc_now()
        with self._conn:
            self._conn.execute(
                """
                UPDATE remote_nodes SET status = 'online', last_seen_at = ?
                WHERE node_id = ?
                """,
                (now, node_id),
            )
        return self.get(node_id)

    def reconnect(
        self,
        node_id: str,
        *,
        lease_seconds: int = 300,
    ) -> RemoteNode:
        self.get(node_id)
        now = datetime.now(timezone.utc)
        lease_until = (now + timedelta(seconds=lease_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO node_connections
                    (node_id, connected_at, lease_until)
                VALUES (?, ?, ?)
                """,
                (node_id, now.strftime("%Y-%m-%dT%H:%M:%SZ"), lease_until),
            )
            self._conn.execute(
                "UPDATE remote_nodes SET status = 'online', last_seen_at = ? "
                "WHERE node_id = ?",
                (now.strftime("%Y-%m-%dT%H:%M:%SZ"), node_id),
            )
        return self.get(node_id)

    def reconcile_connections(self, *, now: str | None = None) -> list[str]:
        now = now or utc_now()
        rows = self._conn.execute(
            "SELECT node_id FROM node_connections WHERE lease_until < ?",
            (now,),
        ).fetchall()
        node_ids = [str(row["node_id"]) for row in rows]
        for node_id in node_ids:
            self._conn.execute(
                "UPDATE remote_nodes SET status = 'offline' WHERE node_id = ?",
                (node_id,),
            )
        self._conn.commit()
        return node_ids

    def get(self, node_id: str) -> RemoteNode:
        row = self._conn.execute(
            "SELECT * FROM remote_nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        if row is None:
            raise KeyError(node_id)
        data = dict(row)
        data["capabilities"] = tuple(json.loads(data["capabilities"]))
        return RemoteNode(**data)

    def list(self) -> list[RemoteNode]:
        rows = self._conn.execute(
            "SELECT * FROM remote_nodes ORDER BY created_at"
        ).fetchall()
        nodes = []
        for row in rows:
            data = dict(row)
            data["capabilities"] = tuple(json.loads(data["capabilities"]))
            nodes.append(RemoteNode(**data))
        return nodes

    def mark_offline(self, node_id: str) -> RemoteNode:
        with self._conn:
            self._conn.execute(
                "UPDATE remote_nodes SET status = 'offline' WHERE node_id = ?",
                (node_id,),
            )
        return self.get(node_id)

    def lease(self, node_id: str, task_ref: str, lease_seconds: int = 300) -> TaskLease:
        lease = TaskLease(
            lease_id=f"lease_{uuid4().hex[:12]}",
            node_id=node_id,
            task_ref=task_ref,
            expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO task_leases (lease_id, node_id, task_ref, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    lease.lease_id,
                    lease.node_id,
                    lease.task_ref,
                    lease.expires_at,
                    lease.created_at,
                ),
            )
        return lease

    def save_dispatch(
        self,
        node_id: str,
        task_ref: str,
        payload: dict,
        lease_seconds: int = 300,
    ) -> TaskLease:
        """Persist a dispatched task together with its lease so the node can
        poll for real work instead of relying on out-of-band configuration."""
        lease = self.lease(
            node_id,
            task_ref,
            lease_seconds=lease_seconds,
        )
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO task_payloads
                    (task_ref, node_id, payload, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    task_ref,
                    node_id,
                    json.dumps(payload, ensure_ascii=True),
                    utc_now(),
                ),
            )
        return lease

    def pending_tasks(
        self,
        node_id: str,
        *,
        now: str | None = None,
    ) -> list[dict]:
        """Return active leased tasks with their payloads for a node."""
        now = now or utc_now()
        rows = self._conn.execute(
            """
            SELECT t.lease_id, t.task_ref, t.expires_at, p.payload,
                   MAX(t.created_at) AS created_at
            FROM task_leases t
            JOIN task_payloads p ON p.task_ref = t.task_ref
            WHERE t.node_id = ? AND t.expires_at >= ?
            GROUP BY t.task_ref
            ORDER BY created_at
            """,
            (node_id, now),
        ).fetchall()
        return [
            {
                "lease_id": str(row["lease_id"]),
                "task_ref": str(row["task_ref"]),
                "expires_at": str(row["expires_at"]),
                "payload": json.loads(str(row["payload"])),
            }
            for row in rows
        ]

    def complete_lease(self, node_id: str, task_ref: str) -> bool:
        """Remove a dispatched task and its lease after the node reports a result."""
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM task_leases WHERE node_id = ? AND task_ref = ?",
                (node_id, task_ref),
            )
            self._conn.execute(
                "DELETE FROM task_payloads WHERE task_ref = ?",
                (task_ref,),
            )
        return cursor.rowcount > 0

    def expire_leases(self, *, now: str | None = None) -> int:
        now = now or utc_now()
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM task_leases WHERE expires_at < ?",
                (now,),
            )
        return cursor.rowcount

    def save_result(self, result: NodeResult) -> NodeResult:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO node_results
                    (result_id, node_id, task_ref, status, artifact_refs,
                     signature, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.result_id,
                    result.node_id,
                    result.task_ref,
                    result.status,
                    json.dumps(result.artifact_refs),
                    result.signature,
                    json.dumps(result.payload, ensure_ascii=True),
                ),
            )
        self.complete_lease(result.node_id, result.task_ref)
        return result

    def get_result(self, result_id: str) -> NodeResult:
        row = self._conn.execute(
            "SELECT * FROM node_results WHERE result_id = ?",
            (result_id,),
        ).fetchone()
        if row is None:
            raise KeyError(result_id)
        data = dict(row)
        data["artifact_refs"] = tuple(json.loads(data["artifact_refs"]))
        data["payload"] = json.loads(data["payload"])
        return NodeResult(**data)

    def list_results(self, node_id: str) -> list[NodeResult]:
        rows = self._conn.execute(
            "SELECT * FROM node_results WHERE node_id = ? ORDER BY result_id",
            (node_id,),
        ).fetchall()
        results: list[NodeResult] = []
        for row in rows:
            data = dict(row)
            data["artifact_refs"] = tuple(json.loads(data["artifact_refs"]))
            data["payload"] = json.loads(data["payload"])
            results.append(NodeResult(**data))
        return results


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
