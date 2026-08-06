from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .thread_safe_sqlite import ThreadSafeSqliteConnection


SCHEMA = """
CREATE TABLE IF NOT EXISTS web_observations (
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, request_id)
);
"""


class WebObservationStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = ThreadSafeSqliteConnection(db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert(self, run_id: str, observation: dict[str, Any]) -> None:
        request_id = str(observation["request_id"])
        with self._conn:
            self._conn.execute(
                "INSERT INTO web_observations (run_id, request_id, payload, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (run_id, request_id) DO UPDATE SET payload = excluded.payload",
                (
                    run_id,
                    request_id,
                    json.dumps(observation, ensure_ascii=True, sort_keys=True),
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
            )

    def list(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM web_observations WHERE run_id = ? "
            "ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def delete_run(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM web_observations WHERE run_id = ?",
                (run_id,),
            )

    def count(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) FROM web_observations").fetchone()[0]
        )
