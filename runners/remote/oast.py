from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .models import CallbackRecord, OastToken, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS callbacks (
    callback_id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oast_tokens (
    token TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    purpose TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
"""


class OastTokenError(RuntimeError):
    pass


class OastStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def record(self, *, token: str, source: str, payload: dict | None = None) -> CallbackRecord:
        record = CallbackRecord(
            callback_id=f"cb_{uuid4().hex[:12]}",
            token=token,
            source=source,
            observed_at=utc_now(),
            payload=payload or {},
        )
        import json

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO callbacks (callback_id, token, source, observed_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.callback_id,
                    record.token,
                    record.source,
                    record.observed_at,
                    json.dumps(record.payload, ensure_ascii=True),
                ),
            )
        return record

    def find(self, token: str) -> list[CallbackRecord]:
        import json

        rows = self._conn.execute(
            "SELECT * FROM callbacks WHERE token = ? ORDER BY observed_at",
            (token,),
        ).fetchall()
        records = []
        for row in rows:
            data = dict(row)
            data["payload"] = json.loads(data["payload"])
            records.append(CallbackRecord(**data))
        return records

    def issue_token(
        self,
        *,
        source: str = "http",
        purpose: str = "",
        ttl_seconds: int = 300,
    ) -> OastToken:
        token = f"oast_{uuid4().hex[:24]}"
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        issued_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO oast_tokens
                    (token, source, purpose, expires_at, issued_at, used)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (token, source, purpose, expires_at, issued_at),
            )
        return OastToken(
            token=token,
            source=source,
            purpose=purpose,
            expires_at=expires_at,
            issued_at=issued_at,
        )

    def redeem(
        self,
        token: str,
        *,
        source: str | None = None,
        payload: dict | None = None,
    ) -> CallbackRecord:
        row = self._conn.execute(
            "SELECT * FROM oast_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None:
            raise OastTokenError("unknown token")
        if row["used"]:
            raise OastTokenError("token already used")
        if row["expires_at"] < utc_now():
            raise OastTokenError("token expired")
        with self._conn:
            self._conn.execute(
                "UPDATE oast_tokens SET used = 1 WHERE token = ?",
                (token,),
            )
        return self.record(
            token=token,
            source=source or row["source"],
            payload=payload,
        )
