from __future__ import annotations

from services.control_plane.app.thread_safe_sqlite import SqliteResult

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS migrations (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class MigrationRecord:
    id: str
    version: str
    description: str
    applied_at: str


class MigrationStore:
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

    def apply(self, migration_id: str, version: str, description: str, forward) -> MigrationRecord:
        if self.is_applied(migration_id):
            raise ValueError(f"migration {migration_id} already applied")
        forward()
        record = MigrationRecord(
            id=migration_id,
            version=version,
            description=description,
            applied_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        with self._lock, self._conn:
            self._execute(
                "INSERT INTO migrations (id, version, description, applied_at) VALUES (?, ?, ?, ?)",
                (record.id, record.version, record.description, record.applied_at),
            )
        return record

    def rollback(self, migration_id: str, backward) -> None:
        if not self.is_applied(migration_id):
            raise ValueError(f"migration {migration_id} is not applied")
        backward()
        with self._lock, self._conn:
            self._execute("DELETE FROM migrations WHERE id = ?", (migration_id,))

    def is_applied(self, migration_id: str) -> bool:
        row = self._execute(
            "SELECT 1 FROM migrations WHERE id = ?",
            (migration_id,),
        ).fetchone()
        return row is not None

    def list_applied(self) -> list[MigrationRecord]:
        rows = self._execute(
            "SELECT * FROM migrations ORDER BY applied_at"
        ).fetchall()
        return [MigrationRecord(**dict(row)) for row in rows]
