from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .thread_safe_sqlite import ThreadSafeSqliteConnection
from .domain import new_id, utc_now


ASSET_LIFECYCLE = (
    "known",
    "in_scope",
    "discovered",
    "scanning",
    "verified",
    "vulnerable",
    "exploited",
    "out_of_scope",
    "retired",
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'known',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_project_value
    ON assets(project_id, kind, value);
CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id);
"""


class AssetStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = ThreadSafeSqliteConnection(db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert(
        self,
        *,
        project_id: str,
        kind: str,
        value: str,
        source: str = "manual",
        status: str = "known",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        existing = self._conn.execute(
            "SELECT asset_id FROM assets WHERE project_id = ? AND kind = ? AND value = ?",
            (project_id, kind, value),
        ).fetchone()
        asset_id = existing["asset_id"] if existing is not None else new_id("asset")
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO assets
                    (asset_id, project_id, kind, value, metadata, source, status,
                     first_seen, last_seen, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (asset_id) DO UPDATE SET
                    metadata = excluded.metadata,
                    source = excluded.source,
                    status = excluded.status,
                    last_seen = excluded.last_seen,
                    updated_at = excluded.updated_at
                """,
                (
                    asset_id,
                    project_id,
                    kind,
                    value,
                    json.dumps(metadata or {}, ensure_ascii=True),
                    source,
                    status,
                    now,
                    now,
                    now,
                ),
            )
        return self.get(asset_id)

    def get(self, asset_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        return self._row(row) if row is not None else None

    def list(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self._conn.execute(
                "SELECT * FROM assets WHERE project_id = ? ORDER BY last_seen DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM assets ORDER BY last_seen DESC"
            ).fetchall()
        return [self._row(row) for row in rows]

    def update(
        self,
        asset_id: str,
        *,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.get(asset_id)
        if current is None:
            raise KeyError(asset_id)
        next_status = status or current["status"]
        next_metadata = dict(current.get("metadata") or {})
        if metadata is not None:
            next_metadata.update(metadata)
        now = utc_now()
        history = list(next_metadata.get("lifecycle_history") or [])
        if not history or history[-1].get("status") != next_status:
            history.append({"status": next_status, "at": now})
        next_metadata["lifecycle_history"] = history[-20:]
        next_metadata["lifecycle_updated_at"] = now
        with self._conn:
            self._conn.execute(
                """
                UPDATE assets
                SET status = ?, metadata = ?, updated_at = ?
                WHERE asset_id = ?
                """,
                (next_status, json.dumps(next_metadata, ensure_ascii=True), now, asset_id),
            )
        return self.get(asset_id)

    def delete(self, asset_id: str) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM assets WHERE asset_id = ?",
                (asset_id,),
            )
        return cursor.rowcount > 0

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["metadata"] = json.loads(data["metadata"])
        except Exception:
            data["metadata"] = {}
        return data
