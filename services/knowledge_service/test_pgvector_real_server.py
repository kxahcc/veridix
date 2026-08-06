from __future__ import annotations

import os

import pytest

from services.knowledge_service.pgvector_store import PgvectorVectorStore


@pytest.mark.integration
def test_pgvector_real_server_upsert_and_search() -> None:
    database_url = os.environ.get("VERIDIX_PGVECTOR_URL")
    if not database_url:
        pytest.skip("VERIDIX_PGVECTOR_URL is not set")

    store = PgvectorVectorStore(database_url=database_url)
    store.upsert("chunk_alpha", [1.0, 0.0], "source_a")
    store.upsert("chunk_beta", [0.0, 1.0], "source_b")

    results = store.search([1.0, 0.0], limit=2)

    assert results[0][0] == "chunk_alpha"
    assert results[0][1] > 0.99
    assert results[1][0] == "chunk_beta"
    assert results[1][1] < 0.01
