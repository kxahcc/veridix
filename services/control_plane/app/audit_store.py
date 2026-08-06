from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .thread_safe_sqlite import ThreadSafeSqliteConnection


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
"""


class AuditLogStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = ThreadSafeSqliteConnection(db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def record(
        self,
        *,
        actor: str,
        action: str,
        resource: str,
        detail: str = "",
        ip: str = "",
    ) -> dict[str, Any]:
        from .contracts import utc_now

        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO audit_logs
                    (actor, action, resource, detail, ip, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (actor, action, resource, detail, ip, utc_now()),
            )
        return self.get(int(cursor.lastrowid))

    def get(self, audit_id: int) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM audit_logs WHERE audit_id = ?",
            (audit_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def list(
        self,
        *,
        limit: int = 100,
        action: str | None = None,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit_logs"
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def to_json(self, row: dict[str, Any]) -> str:
        return json.dumps(row, ensure_ascii=True)
