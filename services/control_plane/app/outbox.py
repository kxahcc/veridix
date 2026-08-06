from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .thread_safe_sqlite import ThreadSafeSqliteConnection


@dataclass(frozen=True)
class OutboxRecord:
    outbox_id: int
    aggregate_id: str
    event_type: str
    payload: dict
    created_at: str
    published_at: str | None


class OutboxStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = ThreadSafeSqliteConnection(db_path)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                aggregate_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                published_at TEXT
            );
            CREATE TABLE IF NOT EXISTS inbox (
                consumer_id TEXT NOT NULL,
                outbox_id INTEGER NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY (consumer_id, outbox_id)
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def enqueue(
        self,
        aggregate_id: str,
        event_type: str,
        payload: dict,
    ) -> OutboxRecord:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO outbox (aggregate_id, event_type, payload, created_at, published_at)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (aggregate_id, event_type, json.dumps(payload, ensure_ascii=True), now),
            )
            outbox_id = cursor.lastrowid
        return OutboxRecord(
            outbox_id=outbox_id,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            created_at=now,
            published_at=None,
        )

    def pending(self, limit: int = 100) -> list[OutboxRecord]:
        rows = self._conn.execute(
            "SELECT * FROM outbox WHERE published_at IS NULL ORDER BY outbox_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._record(row) for row in rows]

    def drain(self, consumer, *, consumer_id: str = "default") -> int:
        published = 0
        for record in self.pending():
            with self._conn:
                row = self._conn.execute(
                    "SELECT 1 FROM inbox WHERE consumer_id = ? AND outbox_id = ?",
                    (consumer_id, record.outbox_id),
                ).fetchone()
                if row is not None:
                    continue
            consumer(record)
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE outbox SET published_at = ? WHERE outbox_id = ? AND published_at IS NULL
                    """,
                    (
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        record.outbox_id,
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO inbox (consumer_id, outbox_id, processed_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        consumer_id,
                        record.outbox_id,
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ),
                )
            published += 1
        return published

    def _record(self, row: sqlite3.Row) -> OutboxRecord:
        return OutboxRecord(
            outbox_id=row["outbox_id"],
            aggregate_id=row["aggregate_id"],
            event_type=row["event_type"],
            payload=json.loads(row["payload"]),
            created_at=row["created_at"],
            published_at=row["published_at"],
        )
