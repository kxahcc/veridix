from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .thread_safe_sqlite import ThreadSafeSqliteConnection
from pydantic import BaseModel, Field

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_type TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    version TEXT NOT NULL,
    hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (snapshot_type, snapshot_id)
);
"""


class SnapshotRecord(BaseModel):
    snapshot_type: str
    snapshot_id: str
    version: str
    hash: str
    payload: dict = Field(default_factory=dict)
    created_at: str


def canonical_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class SnapshotStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = ThreadSafeSqliteConnection(db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save(
        self,
        snapshot_type: str,
        snapshot_id: str,
        version: str,
        payload: dict[str, Any],
    ) -> SnapshotRecord:
        record = SnapshotRecord(
            snapshot_type=snapshot_type,
            snapshot_id=snapshot_id,
            version=version,
            hash=canonical_hash(payload),
            payload=payload,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO snapshots
                    (snapshot_type, snapshot_id, version, hash, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.snapshot_type,
                    record.snapshot_id,
                    record.version,
                    record.hash,
                    json.dumps(record.payload, ensure_ascii=True),
                    record.created_at,
                ),
            )
        return record

    def load(self, snapshot_type: str, snapshot_id: str) -> SnapshotRecord | None:
        row = self._conn.execute(
            """
            SELECT * FROM snapshots
            WHERE snapshot_type = ? AND snapshot_id = ?
            """,
            (snapshot_type, snapshot_id),
        ).fetchone()
        return self._record(row) if row is not None else None

    def latest(self, snapshot_type: str) -> SnapshotRecord | None:
        row = self._conn.execute(
            """
            SELECT * FROM snapshots
            WHERE snapshot_type = ?
            ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (snapshot_type,),
        ).fetchone()
        return self._record(row) if row is not None else None

    def all(self) -> list[SnapshotRecord]:
        rows = self._conn.execute(
            "SELECT * FROM snapshots ORDER BY snapshot_type, created_at"
        ).fetchall()
        return [self._record(row) for row in rows]

    def _record(self, row: sqlite3.Row) -> SnapshotRecord:
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        return SnapshotRecord(**data)
