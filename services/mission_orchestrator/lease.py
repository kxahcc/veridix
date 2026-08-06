from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class NodeLease:
    node_id: str
    worker_id: str
    lease_until: str


class LeaseRegistry:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS node_leases (
                node_id TEXT PRIMARY KEY,
                worker_id TEXT NOT NULL,
                lease_until TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def acquire(
        self,
        node_id: str,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> NodeLease | None:
        now = datetime.now(timezone.utc)
        lease_until = (now + timedelta(seconds=lease_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT lease_until FROM node_leases WHERE node_id = ?",
                (node_id,),
            ).fetchone()
            if existing is not None and existing["lease_until"] >= now.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ):
                return None
            self._conn.execute(
                """
                INSERT OR REPLACE INTO node_leases (node_id, worker_id, lease_until)
                VALUES (?, ?, ?)
                """,
                (node_id, worker_id, lease_until),
            )
        return NodeLease(node_id=node_id, worker_id=worker_id, lease_until=lease_until)

    def release(self, node_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM node_leases WHERE node_id = ?",
                (node_id,),
            )

    def expire(self, *, now: str | None = None) -> int:
        now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM node_leases WHERE lease_until < ?",
                (now,),
            )
        return cursor.rowcount
