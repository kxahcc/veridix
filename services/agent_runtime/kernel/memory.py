from __future__ import annotations

from services.control_plane.app.thread_safe_sqlite import SqliteResult

import json
import os
import sqlite3
import threading
from typing import Any
from pathlib import Path

from services.control_plane.app.contracts import AgentEvent, utc_now

from .contracts import Checkpoint
from .ports import CheckpointStorePort, EventSinkPort


class InMemoryEventSink(EventSinkPort):
    def __init__(self) -> None:
        self._events: dict[str, list[AgentEvent]] = {}

    def emit(
        self,
        *,
        stream_id: str,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> AgentEvent:
        events = self._events.setdefault(stream_id, [])
        sequence = len(events) + 1
        event = AgentEvent(
            event_id=f"evt_{run_id}_{sequence}",
            event_type=event_type,
            stream_id=stream_id,
            run_id=run_id,
            actor=actor,
            occurred_at=utc_now(),
            sequence=sequence,
            payload=payload,
        )
        events.append(event)
        return event

    def replay(self, stream_id: str) -> list[AgentEvent]:
        return list(self._events.get(stream_id, []))

    def latest_sequence(self, stream_id: str) -> int:
        return len(self._events.get(stream_id, []))


class InMemoryCheckpointStore(CheckpointStorePort):
    def __init__(self) -> None:
        self._checkpoints: dict[str, Checkpoint] = {}

    def save(self, checkpoint: Checkpoint) -> None:
        self._checkpoints[checkpoint.run_id] = checkpoint

    def load(self, run_id: str) -> Checkpoint | None:
        return self._checkpoints.get(run_id)


class FileCheckpointStore(CheckpointStorePort):
    """Checkpoints written under runtime/checkpoints/<run-id>/checkpoint.json."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(self, checkpoint: Checkpoint) -> None:
        path = self._root / checkpoint.run_id / "checkpoint.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": checkpoint.run_id,
            "cursor": checkpoint.cursor,
            "state": checkpoint.state,
            "transcript": [
                dict(item) for item in checkpoint.transcript
            ],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def load(self, run_id: str) -> Checkpoint | None:
        path = self._root / run_id / "checkpoint.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint(
            run_id=data["run_id"],
            cursor=int(data["cursor"]),
            state=dict(data["state"]),
            transcript=tuple(
                dict(item) for item in data.get("transcript") or ()
            ),
        )


class SqliteCheckpointStore(CheckpointStorePort):
    """Versioned checkpoint persistence in SQLite.

    Each save creates a new version for the run; `load` returns the latest.
    This mirrors durable, replayable checkpointer semantics used by mature
    agent frameworks while staying local and easy to inspect.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS agent_checkpoints (
        run_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        cursor INTEGER NOT NULL,
        state TEXT NOT NULL,
        transcript TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, version)
    );
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    def _execute(self, sql: str, params=()):
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return SqliteResult(cursor.fetchall(), cursor.rowcount)

    def close(self) -> None:
        self._conn.close()

    def save(self, checkpoint: Checkpoint) -> None:
        row = self._execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1
            FROM agent_checkpoints
            WHERE run_id = ?
            """,
            (checkpoint.run_id,),
        ).fetchone()
        version = int(row[0])
        with self._lock, self._conn:
            self._execute(
                """
                INSERT OR REPLACE INTO agent_checkpoints (
                    run_id, version, cursor, state, transcript, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.run_id,
                    version,
                    checkpoint.cursor,
                    json.dumps(checkpoint.state, ensure_ascii=True, sort_keys=True),
                    json.dumps(
                        [dict(item) for item in checkpoint.transcript],
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                    utc_now(),
                ),
            )

    def load(self, run_id: str) -> Checkpoint | None:
        row = self._execute(
            """
            SELECT version, cursor, state, transcript
            FROM agent_checkpoints
            WHERE run_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return self._from_row(run_id, row)

    def versions(self, run_id: str) -> tuple[dict[str, Any], ...]:
        rows = self._execute(
            """
            SELECT version, cursor, created_at
            FROM agent_checkpoints
            WHERE run_id = ?
            ORDER BY version ASC
            """,
            (run_id,),
        ).fetchall()
        return tuple(
            {
                "version": int(row[0]),
                "cursor": int(row[1]),
                "created_at": str(row[2]),
            }
            for row in rows
        )

    def load_version(self, run_id: str, version: int) -> Checkpoint | None:
        row = self._execute(
            """
            SELECT version, cursor, state, transcript
            FROM agent_checkpoints
            WHERE run_id = ? AND version = ?
            """,
            (run_id, int(version)),
        ).fetchone()
        if row is None:
            return None
        return self._from_row(run_id, row)

    def _from_row(self, run_id: str, row: tuple) -> Checkpoint:
        _, cursor, state_json, transcript_json = row
        return Checkpoint(
            run_id=run_id,
            cursor=int(cursor),
            state=json.loads(state_json),
            transcript=tuple(
                dict(item) for item in json.loads(transcript_json)
            ),
        )
