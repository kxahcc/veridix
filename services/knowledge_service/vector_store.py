from __future__ import annotations

from services.control_plane.app.thread_safe_sqlite import SqliteResult

import json
import math
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, chunk_id: str, vector: list[float], source_ref: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(self, query_vector: list[float], limit: int = 5) -> list[tuple[str, float]]:
        raise NotImplementedError

    @abstractmethod
    def delete(self, chunk_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        raise NotImplementedError


class SqliteVectorStore(VectorStore):
    """Desktop vector store; PostgreSQL/pgvector is the Server adapter."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS chunk_vectors (
                chunk_id TEXT PRIMARY KEY,
                vector TEXT NOT NULL,
                source_ref TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def _execute(self, sql: str, params=()):
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return SqliteResult(cursor.fetchall(), cursor.rowcount)

    def close(self) -> None:
        self._conn.close()

    def upsert(self, chunk_id: str, vector: list[float], source_ref: str) -> None:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT OR REPLACE INTO chunk_vectors (chunk_id, vector, source_ref)
                VALUES (?, ?, ?)
                """,
                (chunk_id, json.dumps(vector), source_ref),
            )

    def search(self, query_vector: list[float], limit: int = 5) -> list[tuple[str, float]]:
        rows = self._execute(
            "SELECT chunk_id, vector FROM chunk_vectors"
        ).fetchall()
        scored: list[tuple[str, float]] = []
        for row in rows:
            vector = json.loads(row["vector"])
            scored.append((row["chunk_id"], _cosine(query_vector, vector)))
        return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]

    def delete(self, chunk_id: str) -> None:
        with self._lock, self._conn:
            self._execute(
                "DELETE FROM chunk_vectors WHERE chunk_id = ?",
                (chunk_id,),
            )

    def count(self) -> int:
        row = self._execute(
            "SELECT COUNT(*) FROM chunk_vectors"
        ).fetchone()
        return int(row[0])


def _cosine(first: list[float], second: list[float]) -> float:
    dot = sum(a * b for a, b in zip(first, second))
    first_norm = math.sqrt(sum(a * a for a in first))
    second_norm = math.sqrt(sum(b * b for b in second))
    if first_norm == 0 or second_norm == 0:
        return 0.0
    return dot / (first_norm * second_norm)
