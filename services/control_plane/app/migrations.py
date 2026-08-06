from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .thread_safe_sqlite import ThreadSafeSqliteConnection


MIGRATIONS: dict[str, list[str]] = {
    "2": [
        "ALTER TABLE events ADD COLUMN trace_id TEXT",
    ],
}


class SchemaMigrator:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = ThreadSafeSqliteConnection(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def current_version(self) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
        return row["version"] if row is not None else "1"

    def apply_all(self) -> list[str]:
        applied: list[str] = []
        current = self.current_version()
        for version, statements in sorted(MIGRATIONS.items()):
            if version <= current:
                continue
            with self._lock, self._conn:
                for statement in statements:
                    self._conn.execute(statement)
                self._conn.execute(
                    """
                    INSERT INTO schema_migrations (version, applied_at)
                    VALUES (?, ?)
                    """,
                    (version, _now()),
                )
            applied.append(version)
        return applied


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
