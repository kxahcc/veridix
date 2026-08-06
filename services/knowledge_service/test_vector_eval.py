from __future__ import annotations

from services.knowledge_service.evaluation import (
    EvaluationQuery,
    RetrievalEvaluator,
)
from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.models import KnowledgeChunk
from services.knowledge_service.retrieval import RetrievalEngine
from services.knowledge_service.vector_store import SqliteVectorStore


def test_sqlite_vector_store_upsert_and_search() -> None:
    store = SqliteVectorStore(":memory:")
    store.upsert("near", [1.0, 0.0], "docs/admin")
    store.upsert("far", [0.0, 1.0], "docs/other")

    results = store.search([1.0, 0.0], limit=2)

    assert results[0][0] == "near"
    assert results[0][1] > results[1][1]
    store.close()


def test_retrieval_evaluation_reports_hit_rate_and_p95() -> None:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="admin",
            source_ref="docs/admin",
            content="admin panel default credentials",
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="other",
            source_ref="docs/other",
            content="unrelated topic",
        )
    )
    engine = RetrievalEngine(store)
    evaluator = RetrievalEvaluator(engine)

    result = evaluator.evaluate(
        [
            EvaluationQuery("admin panel", ("admin",)),
            EvaluationQuery("topic", ("other",)),
        ],
        target_ref="t",
        node_type="web_discovery",
    )

    assert result.queries == 2
    assert result.hit_rate == 1.0
    assert result.p95_latency_ms > 0
    assert result.token_estimate > 0
