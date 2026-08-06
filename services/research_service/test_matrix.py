from __future__ import annotations

from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.evaluation import EvaluationQuery
from services.knowledge_service.models import KnowledgeChunk
from services.knowledge_service.retrieval import (
    LocalEmbeddingAdapter,
)
from services.research_service.matrix import MatrixRunner, ModelProfile


class FakeRerank:
    def rerank(self, query: str, chunks):
        return [
            (chunk.chunk_id, 1.0 if "admin" in chunk.content else 0.0)
            for chunk in chunks
        ]


class FakeEmbedding(LocalEmbeddingAdapter):
    def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0]


class FakeQdrantStore:
    def search(
        self,
        query_vector: list[float],
        limit: int = 5,
    ) -> list[tuple[str, float]]:
        return [("admin", 1.0), ("other", 0.0)][:limit]

    def search_hybrid(
        self,
        query_vector: list[float],
        *,
        indices: list[int],
        values: list[float],
        limit: int = 5,
    ) -> list[tuple[str, float]]:
        return self.search(query_vector, limit=limit)


def test_matrix_runner_covers_four_rag_levels() -> None:
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
    runner = MatrixRunner(
        store,
        embedding=FakeEmbedding(),
        rerank=FakeRerank(),
        vector_store=FakeQdrantStore(),
        queries=[
            EvaluationQuery("admin panel", ("admin",)),
            EvaluationQuery("topic", ("other",)),
        ],
    )

    report = runner.run(
        [ModelProfile(name="fixture", provider="mock", backend="local")],
        target_ref="t",
        node_type="web_discovery",
    )

    assert {row.rag_level for row in report.rows} == {
        "lexical",
        "embedding",
        "embedding_rerank",
        "qdrant_hybrid",
    }
    assert all(row.model == "fixture" for row in report.rows)
    assert all(row.hit_rate == 1.0 for row in report.rows)
