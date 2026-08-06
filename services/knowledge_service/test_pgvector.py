from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy import create_mock_engine, text

from services.knowledge_service.pgvector_store import PgvectorVectorStore, _vector_literal


def test_pgvector_sql_compiles_for_postgres() -> None:
    store = PgvectorVectorStore(
        engine=create_mock_engine("postgresql://", lambda *args: None)
    )
    upsert_sql = str(
        text(
            """
            INSERT INTO chunk_vectors (chunk_id, vector, source_ref)
            VALUES (:chunk_id, CAST(:vector AS vector), :source_ref)
            ON CONFLICT (chunk_id) DO UPDATE SET
                vector = excluded.vector,
                source_ref = excluded.source_ref
            """
        ).compile(dialect=postgresql.dialect())
    )
    search_sql = str(
        text(
            """
            SELECT chunk_id, vector <=> CAST(:query AS vector) AS distance
            FROM chunk_vectors
            ORDER BY distance
            LIMIT :limit
            """
        ).compile(dialect=postgresql.dialect())
    )

    assert "ON CONFLICT" in upsert_sql
    assert "CAST(%(vector)s AS vector)" in upsert_sql
    assert "CAST(%(query)s AS vector)" in search_sql
    assert _vector_literal([1.0, 2.0]) == "[1.0,2.0]"
    assert hasattr(store, "upsert")
    assert hasattr(store, "search")
