from __future__ import annotations

from typing import Any

from .vector_store import VectorStore


class PgvectorVectorStore(VectorStore):
    """Server-side pgvector adapter. Requires a PostgreSQL + pgvector host."""

    def __init__(self, database_url: str | None = None, engine=None) -> None:
        if engine is not None:
            self._engine = engine
        else:
            from sqlalchemy import create_engine
            from sqlalchemy.engine import make_url

            if database_url is None:
                raise ValueError("database_url or engine is required")
            parsed = make_url(database_url)

            def creator():
                import psycopg2

                return psycopg2.connect(
                    host=parsed.host,
                    port=parsed.port or 5432,
                    user=parsed.username,
                    password=parsed.password,
                    dbname=parsed.database,
                    connect_timeout=30,
                )

            self._engine = create_engine(
                "postgresql+psycopg2://",
                creator=creator,
                pool_pre_ping=True,
            )

    def upsert(self, chunk_id: str, vector: list[float], source_ref: str) -> None:
        from sqlalchemy import text

        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE EXTENSION IF NOT EXISTS vector
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS chunk_vectors (
                        chunk_id TEXT PRIMARY KEY,
                        vector vector NOT NULL,
                        source_ref TEXT NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO chunk_vectors (chunk_id, vector, source_ref)
                    VALUES (:chunk_id, CAST(:vector AS vector), :source_ref)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        vector = excluded.vector,
                        source_ref = excluded.source_ref
                    """
                ),
                {
                    "chunk_id": chunk_id,
                    "vector": _vector_literal(vector),
                    "source_ref": source_ref,
                },
            )

    def search(self, query_vector: list[float], limit: int = 5) -> list[tuple[str, float]]:
        from sqlalchemy import text

        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT chunk_id, vector <=> CAST(:query AS vector) AS distance
                    FROM chunk_vectors
                    WHERE vector_dims(vector) = :dim
                    ORDER BY distance
                    LIMIT :limit
                    """
                ),
                {
                    "query": _vector_literal(query_vector),
                    "dim": len(query_vector),
                    "limit": limit,
                },
            ).fetchall()
        return [(row[0], round(1.0 - float(row[1]), 6)) for row in rows]

    def delete(self, chunk_id: str) -> None:
        from sqlalchemy import text

        with self._engine.begin() as connection:
            connection.execute(
                text("DELETE FROM chunk_vectors WHERE chunk_id = :chunk_id"),
                {"chunk_id": chunk_id},
            )

    def count(self) -> int:
        from sqlalchemy import text

        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    text("SELECT COUNT(*) FROM chunk_vectors")
                ).fetchone()
            return int(row[0])
        except Exception:
            return 0


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(value) for value in vector) + "]"
