from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .thread_safe_sqlite import ThreadSafeSqliteConnection
from .contracts import AgentEvent, CommandEnvelope, CommandRecord, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream_id, sequence);

CREATE TABLE IF NOT EXISTS commands (
    command_id TEXT PRIMARY KEY,
    command_type TEXT NOT NULL,
    run_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    accepted_at TEXT,
    rejected_reason TEXT,
    payload TEXT NOT NULL
);
"""


class EventStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = ThreadSafeSqliteConnection(db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def append(self, event: AgentEvent) -> AgentEvent:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO events
                    (event_id, schema_version, event_type, stream_id, run_id,
                     actor, occurred_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.schema_version,
                    event.event_type,
                    event.stream_id,
                    event.run_id,
                    event.actor,
                    event.occurred_at,
                    json.dumps(event.payload, ensure_ascii=True),
                ),
            )
            sequence = cursor.lastrowid
        return event.model_copy(update={"sequence": sequence})

    def replay(
        self,
        stream_id: str,
        *,
        after: int = 0,
        limit: int | None = None,
    ) -> list[AgentEvent]:
        sql = """
            SELECT event_id, schema_version, event_type, stream_id, run_id,
                   actor, occurred_at, sequence, payload
            FROM events
            WHERE stream_id = ? AND sequence > ?
            ORDER BY sequence
        """
        params: list = [stream_id, after]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        events: list[AgentEvent] = []
        for row in rows:
            data = dict(row)
            data["payload"] = json.loads(data["payload"])
            events.append(AgentEvent(**data))
        return events

    def latest_sequence(self, stream_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS seq FROM events WHERE stream_id = ?",
                (stream_id,),
            ).fetchone()
        return int(row["seq"])

    def stream_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT stream_id FROM events ORDER BY stream_id"
            ).fetchall()
        return [str(row["stream_id"]) for row in rows]

    def delete_stream(self, stream_id: str) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM events WHERE stream_id = ?",
                (stream_id,),
            )
        return cursor.rowcount


class CommandStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def submit(self, command: CommandEnvelope) -> tuple[CommandRecord, bool]:
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO commands
                    (command_id, command_type, run_id, idempotency_key, state,
                     requested_at, accepted_at, payload)
                VALUES (?, ?, ?, ?, 'accepted', ?, ?, ?)
                """,
                (
                    command.command_id,
                    command.command_type,
                    command.run_id,
                    command.idempotency_key,
                    command.requested_at,
                    utc_now(),
                    json.dumps(command.payload, ensure_ascii=True),
                ),
            )
            inserted = cursor.rowcount == 1
        record = self.get(command.idempotency_key)
        return record, not inserted

    def get(self, idempotency_key: str) -> CommandRecord:
        row = self._conn.execute(
            "SELECT * FROM commands WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            raise KeyError(f"command {idempotency_key} not found")
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        return CommandRecord(**data)

    def reject(self, idempotency_key: str, reason: str) -> CommandRecord:
        with self._conn:
            self._conn.execute(
                """
                UPDATE commands
                SET state = 'rejected', rejected_reason = ?
                WHERE idempotency_key = ?
                """,
                (reason, idempotency_key),
            )
        return self.get(idempotency_key)
