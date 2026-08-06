from __future__ import annotations

from services.control_plane.app.thread_safe_sqlite import SqliteResult

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class V2LicenseNotRecorded(RuntimeError):
    pass


V2_IMPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS v2_imports (
    migration_id TEXT PRIMARY KEY,
    source_commit TEXT NOT NULL,
    payload TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ImportDecision:
    accepted: bool
    imported: dict[str, Any]
    reason: str


def import_v2_snapshot(
    data: dict[str, Any],
    *,
    license_recorded: bool,
    source_commit: str,
) -> ImportDecision:
    if not license_recorded:
        raise V2LicenseNotRecorded(
            "V2 license must be recorded in the reuse ledger before import"
        )
    imported = {
        "source_commit": source_commit,
        "read_only": True,
        "projects": data.get("projects", []),
        "runs": data.get("runs", []),
    }
    return ImportDecision(accepted=True, imported=imported, reason="adapter_import")


class V2ImportStore:
    def __init__(self, db_path: str | Any = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(V2_IMPORT_SCHEMA)
        self._conn.commit()

    def _execute(self, sql: str, params=()):
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return SqliteResult(cursor.fetchall(), cursor.rowcount)

    def close(self) -> None:
        self._conn.close()

    def write(
        self,
        migration_id: str,
        source_commit: str,
        payload: dict[str, Any],
    ) -> None:
        with self._lock, self._conn:
            self._execute(
                "INSERT OR REPLACE INTO v2_imports "
                "(migration_id, source_commit, payload, imported_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    migration_id,
                    source_commit,
                    json.dumps(payload, ensure_ascii=True, sort_keys=True),
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
            )

    def delete(self, migration_id: str) -> None:
        with self._lock, self._conn:
            self._execute(
                "DELETE FROM v2_imports WHERE migration_id = ?",
                (migration_id,),
            )

    def get(self, migration_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT source_commit, payload, imported_at "
            "FROM v2_imports WHERE migration_id = ?",
            (migration_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "source_commit": row[0],
            "payload": json.loads(row[1]),
            "imported_at": row[2],
        }

    def count(self) -> int:
        return int(
            self._execute("SELECT COUNT(*) FROM v2_imports").fetchone()[0]
        )


def normalize_v2_snapshot(
    data: dict[str, Any],
    *,
    source_commit: str,
) -> dict[str, Any]:
    def veridix_id(kind: str, raw_id: str) -> str:
        digest = hashlib.sha256(
            f"{source_commit}:{kind}:{raw_id}".encode("utf-8")
        ).hexdigest()
        return f"veridix_{kind}_{digest[:20]}"

    projects = [
        {
            "project_id": veridix_id("project", str(item.get("id", ""))),
            "name": str(item.get("name", "")),
            "imported_from": {
                "source_commit": source_commit,
                "v2_id": item.get("id"),
            },
        }
        for item in data.get("projects", [])
    ]
    targets = [
        {
            "target_id": veridix_id("target", str(item.get("id", ""))),
            "url": str(item.get("url", "")),
            "imported_from": {
                "source_commit": source_commit,
                "v2_id": item.get("id"),
            },
        }
        for project in data.get("projects", [])
        for item in project.get("targets", [])
    ]
    runs = [
        {
            "run_id": veridix_id("run", str(item.get("id", ""))),
            "mission_id": veridix_id(
                "mission",
                str(item.get("mission_id", "")),
            ),
            "status": str(item.get("status", "unknown")),
            "event_count": len(item.get("events", [])),
            "imported_from": {
                "source_commit": source_commit,
                "v2_id": item.get("id"),
            },
        }
        for item in data.get("runs", [])
    ]
    return {
        "schema_version": 1,
        "source_commit": source_commit,
        "projects": projects,
        "targets": targets,
        "runs": runs,
        "event_count": sum(run["event_count"] for run in runs),
    }


def apply_v2_migration(
    data: dict[str, Any],
    *,
    db_path: str | Any,
    license_recorded: bool,
    source_commit: str,
):
    from .migrations import MigrationStore

    import_v2_snapshot(
        data,
        license_recorded=license_recorded,
        source_commit=source_commit,
    )
    payload = normalize_v2_snapshot(data, source_commit=source_commit)
    migration_id = f"v2:{source_commit}"
    store = MigrationStore(db_path)
    import_store = V2ImportStore(db_path)
    try:
        def forward() -> None:
            import_store.write(migration_id, source_commit, payload)

        record = store.apply(
            migration_id,
            "v2-1",
            f"import V2 snapshot {source_commit}",
            forward,
        )
    finally:
        import_store.close()
        store.close()
    return record


def rollback_v2_migration(
    *,
    db_path: str | Any,
    migration_id: str,
) -> None:
    from .migrations import MigrationStore

    store = MigrationStore(db_path)
    import_store = V2ImportStore(db_path)
    try:
        def backward() -> None:
            import_store.delete(migration_id)

        store.rollback(migration_id, backward)
    finally:
        import_store.close()
        store.close()
