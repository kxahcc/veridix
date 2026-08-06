from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .thread_safe_sqlite import ThreadSafeSqliteConnection
from .domain import new_id, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    last_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
"""


class SessionStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = ThreadSafeSqliteConnection(db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_for_run(
        self,
        *,
        run_id: str,
        project_id: str,
        title: str,
        last_message: str = "",
    ) -> dict[str, Any]:
        now = utc_now()
        existing = self._conn.execute(
            "SELECT session_id FROM sessions WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        session_id = existing["session_id"] if existing is not None else new_id("session")
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO sessions
                    (session_id, run_id, project_id, title, archived,
                     last_message, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT (run_id) DO UPDATE SET
                    title = excluded.title,
                    last_message = excluded.last_message,
                    updated_at = excluded.updated_at
                """,
                (session_id, run_id, project_id, title, last_message, now, now),
            )
        return self.get_by_run(run_id)

    def touch(
        self,
        run_id: str,
        *,
        last_message: str = "",
    ) -> dict[str, Any] | None:
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE sessions
                SET last_message = CASE
                        WHEN ? <> '' THEN ?
                        ELSE last_message
                    END,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (last_message, last_message, utc_now(), run_id),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_by_run(run_id)

    def get_by_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return self._row(row) if row is not None else None

    def list(
        self,
        *,
        project_id: str | None = None,
        archived: bool | None = False,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sessions WHERE 1=1"
        params: list[Any] = []
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        if archived is not None:
            sql += " AND archived = ?"
            params.append(1 if archived else 0)
        sql += " ORDER BY updated_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row(row) for row in rows]

    def update(
        self,
        session_id: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        updates: list[str] = []
        params: list[Any] = []
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if archived is not None:
            updates.append("archived = ?")
            params.append(1 if archived else 0)
        if not updates:
            return self.get(session_id)
        updates.append("updated_at = ?")
        params.append(utc_now())
        params.append(session_id)
        with self._conn:
            cursor = self._conn.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?",
                params,
            )
        if cursor.rowcount == 0:
            raise KeyError(session_id)
        return self.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
        return cursor.rowcount > 0

    def get(self, session_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return self._row(row) if row is not None else None

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["archived"] = bool(data["archived"])
        return data
