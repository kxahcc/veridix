from __future__ import annotations

from services.control_plane.app.thread_safe_sqlite import SqliteResult

import json
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

from .memory import (
    FactView,
    MemorySnapshot,
    project_fact_views,
)
from .models import FactRecord, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS project_facts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    source_refs TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    trust TEXT NOT NULL DEFAULT 'project_observed',
    observed_at TEXT NOT NULL,
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS project_memory_summaries (
    summary_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_facts_project
    ON project_facts(project_id, subject, predicate);
CREATE INDEX IF NOT EXISTS idx_project_memory_summaries_project
    ON project_memory_summaries(project_id, created_at DESC);
"""


class SqliteProjectMemory:
    """Persistent append-only fact board for one project."""

    def __init__(
        self,
        db_path: str | Path,
        project_id: str,
    ) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        columns = {
            row["name"]
            for row in self._execute(
                "PRAGMA table_info(project_facts)"
            ).fetchall()
        }
        if "metadata" not in columns:
            self._execute(
                "ALTER TABLE project_facts "
                "ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
            )
        self._conn.commit()
        self._project_id = project_id

    def _execute(self, sql: str, params=()):
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return SqliteResult(cursor.fetchall(), cursor.rowcount)

    @property
    def project_id(self) -> str:
        return self._project_id

    def close(self) -> None:
        self._conn.close()

    def append(self, fact: FactRecord) -> FactRecord:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO project_facts (
                    fact_id, project_id, subject, predicate, value, target,
                    source_refs, confidence, trust, observed_at, expires_at,
                    status, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.fact_id,
                    self._project_id,
                    fact.subject,
                    fact.predicate,
                    fact.value,
                    fact.target,
                    json.dumps(list(fact.source_refs), ensure_ascii=True),
                    fact.confidence,
                    fact.trust,
                    fact.observed_at,
                    fact.expires_at,
                    fact.status,
                    json.dumps(
                        dict(fact.metadata or {}),
                        ensure_ascii=True,
                    ),
                ),
            )
        return fact

    def replay(self) -> tuple[FactRecord, ...]:
        rows = self._execute(
            """
            SELECT fact_id, subject, predicate, value, target, source_refs,
                   confidence, trust, observed_at, expires_at, status, metadata
            FROM project_facts
            WHERE project_id = ?
            ORDER BY sequence ASC
            """,
            (self._project_id,),
        ).fetchall()
        return tuple(self._to_fact(row) for row in rows)

    def projection(
        self,
        *,
        now: str | None = None,
        subject: str | None = None,
    ) -> tuple[FactView, ...]:
        return project_fact_views(
            self.replay(),
            now=now,
            subject=subject,
        )

    def facts_for(
        self,
        subject: str,
        *,
        now: str | None = None,
    ) -> tuple[FactView, ...]:
        return tuple(
            view
            for view in self.projection(now=now, subject=subject)
            if view.status in ("active", "conflict")
        )

    def view(
        self,
        *,
        now: str | None = None,
        subject: str | None = None,
    ) -> tuple[FactView, ...]:
        return self.projection(now=now, subject=subject)

    def retract(
        self,
        fact_id: str,
        *,
        reason: str = "human_correction",
    ) -> FactRecord:
        target = self._find(fact_id)
        if target is None:
            raise KeyError(fact_id)
        return self.append(
            FactRecord(
                fact_id=self._next_id("fact_retract"),
                subject=target.subject,
                predicate="retracts",
                value=fact_id,
                target=self._project_id,
                source_refs=(reason,),
                confidence=1.0,
                trust="human",
                observed_at=utc_now(),
                status="retracted",
            )
        )

    def fix(
        self,
        subject: str,
        predicate: str,
        value: str,
        *,
        reason: str = "human_fix",
    ) -> FactRecord:
        for view in self.projection(subject=subject):
            if view.fact.predicate == predicate and view.status in (
                "active",
                "conflict",
            ):
                self.retract(view.fact.fact_id, reason=f"fix:{reason}")
        return self.append(
            FactRecord(
                fact_id=self._next_id("fact_fix"),
                subject=subject,
                predicate=predicate,
                value=value,
                target=self._project_id,
                source_refs=(reason,),
                confidence=1.0,
                trust="human",
                status="active",
            )
        )

    def forget(
        self,
        fact_id: str,
        *,
        reason: str = "human_forget",
    ) -> FactRecord:
        return self.retract(fact_id, reason=reason)

    def mark_stale(
        self,
        fact_id: str,
        *,
        reason: str = "replay_mismatch",
    ) -> FactRecord:
        target = self._find(fact_id)
        if target is None:
            raise KeyError(fact_id)
        return self.append(
            FactRecord(
                fact_id=self._next_id("fact_stale"),
                subject=target.subject,
                predicate="stale",
                value=fact_id,
                target=self._project_id,
                source_refs=(reason,),
                confidence=1.0,
                trust="human",
                observed_at=utc_now(),
            )
        )

    def clear(self, *, reason: str = "memory_cleared") -> int:
        targets = [
            view.fact
            for view in self.projection()
            if view.fact.predicate != "retracts"
        ]
        for fact in targets:
            self.retract(fact.fact_id, reason=reason)
        return len(targets)

    def append_summary(
        self,
        summary: str,
        *,
        source_ref: str = "",
    ) -> dict:
        summary_id = f"summary_{uuid4().hex[:12]}"
        row = {
            "summary_id": summary_id,
            "project_id": self._project_id,
            "source_ref": source_ref,
            "summary": summary,
            "created_at": utc_now(),
        }
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO project_memory_summaries (
                    summary_id, project_id, source_ref, summary, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["summary_id"],
                    row["project_id"],
                    row["source_ref"],
                    row["summary"],
                    row["created_at"],
                ),
            )
        return row

    def summaries(self, *, limit: int = 5) -> tuple[dict, ...]:
        rows = self._execute(
            """
            SELECT summary_id, project_id, source_ref, summary, created_at
            FROM project_memory_summaries
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (self._project_id, max(1, int(limit))),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def snapshot(self, *, now: str | None = None) -> MemorySnapshot:
        statuses = {
            view.fact.fact_id: view.status
            for view in self.projection(now=now)
        }
        return MemorySnapshot(
            project_id=self._project_id,
            total_facts=len(self.replay()),
            active=sum(status == "active" for status in statuses.values()),
            conflict=sum(status == "conflict" for status in statuses.values()),
            stale=sum(status == "stale" for status in statuses.values()),
            taken_at=utc_now(),
        )

    def record(
        self,
        subject: str,
        predicate: str,
        value: str,
        *,
        target: str = "",
        source_refs: tuple[str, ...] = (),
        confidence: float = 0.7,
        trust: str = "project_observed",
        expires_at: str | None = None,
        metadata: dict | None = None,
    ) -> tuple[FactRecord, bool]:
        """Append a fact unless an identical active fact already exists."""
        existing = next(
            (
                view.fact
                for view in self.projection(subject=subject)
                if view.status in ("active", "conflict")
                and view.fact.predicate == predicate
                and view.fact.value == value
            ),
            None,
        )
        if existing is not None:
            return existing, False
        return (
            self.append(
                FactRecord(
                    fact_id=self._next_id("fact_observed"),
                    subject=subject,
                    predicate=predicate,
                    value=value,
                    target=target,
                    source_refs=source_refs,
                    confidence=confidence,
                    trust=trust,
                    expires_at=expires_at,
                    metadata=dict(metadata or {}),
                )
            ),
            True,
        )

    def _find(self, fact_id: str) -> FactRecord | None:
        row = self._execute(
            """
            SELECT fact_id, subject, predicate, value, target, source_refs,
                   confidence, trust, observed_at, expires_at, status, metadata
            FROM project_facts
            WHERE project_id = ? AND fact_id = ?
            """,
            (self._project_id, fact_id),
        ).fetchone()
        return self._to_fact(row) if row is not None else None

    def _next_id(self, prefix: str) -> str:
        row = self._execute(
            "SELECT COUNT(*) AS count FROM project_facts",
        ).fetchone()
        return f"{prefix}_{int(row['count']) + 1}"

    @staticmethod
    def _to_fact(row: sqlite3.Row) -> FactRecord:
        return FactRecord(
            fact_id=row["fact_id"],
            subject=row["subject"],
            predicate=row["predicate"],
            value=row["value"],
            target=row["target"] or "",
            source_refs=_parse_source_refs(row["source_refs"]),
            confidence=float(row["confidence"]),
            trust=row["trust"],
            observed_at=row["observed_at"],
            expires_at=row["expires_at"],
            status=row["status"],
            metadata=(
                json.loads(row["metadata"])
                if row["metadata"]
                else {}
            ),
        )


def _parse_source_refs(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    if raw.startswith("["):
        try:
            return tuple(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return tuple(item for item in raw.split(",") if item)


class ProjectMemoryStore:
    """Multi-project SQLite memory backed by a single database file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._projects: dict[str, SqliteProjectMemory] = {}

    def get(self, project_id: str) -> SqliteProjectMemory:
        memory = self._projects.get(project_id)
        if memory is None:
            memory = SqliteProjectMemory(self._db_path, project_id)
            self._projects[project_id] = memory
        return memory

    def close(self) -> None:
        for memory in self._projects.values():
            memory.close()
        self._projects.clear()
