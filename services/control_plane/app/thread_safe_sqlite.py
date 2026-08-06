from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any


class SqliteResult:
    """In-memory cursor result fetched under a connection lock."""

    def __init__(self, rows: list[Any], rowcount: int = 0) -> None:
        self._rows = list(rows)
        self._rowcount = rowcount
        self._index = 0

    @property
    def rowcount(self) -> int:
        return self._rowcount

    def fetchone(self) -> Any | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[Any]:
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self._rows[self._index :])


class ThreadSafeSqliteConnection:
    """Sqlite connection proxy that serializes access from API threads.

    FastAPI runs endpoint handlers in a thread pool, so a bare connection
    shared by the control-plane stores can hit ``InterfaceError`` when two
    threads execute concurrently. This proxy keeps the same call surface
    while guarding every statement (and full transaction blocks) with a
    re-entrant lock.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=10000")

    @property
    def row_factory(self) -> Any:
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        self._conn.row_factory = value

    @property
    def total_changes(self) -> int:
        with self._lock:
            return self._conn.total_changes

    def execute(self, *args: Any, **kwargs: Any):
        with self._lock:
            return self._conn.execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any):
        with self._lock:
            return self._conn.executemany(*args, **kwargs)

    def executescript(self, *args: Any, **kwargs: Any):
        with self._lock:
            return self._conn.executescript(*args, **kwargs)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def cursor(self):
        with self._lock:
            return self._conn.cursor()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "ThreadSafeSqliteConnection":
        self._lock.acquire()
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._conn.__exit__(exc_type, exc, tb)
        finally:
            self._lock.release()
