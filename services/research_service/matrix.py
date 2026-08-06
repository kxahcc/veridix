from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from services.knowledge_service.evaluation import (
    EvaluationQuery,
    RetrievalEvaluator,
)
from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.retrieval import RetrievalEngine


@dataclass(frozen=True)
class ModelProfile:
    name: str
    provider: str
    backend: str


@dataclass(frozen=True)
class MatrixRow:
    model: str
    rag_level: str
    hit_rate: float
    p95_ms: float
    degraded: int
    token_estimate: int


@dataclass(frozen=True)
class MatrixReport:
    rows: tuple[MatrixRow, ...]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )


RAG_LEVELS = ("lexical", "embedding", "embedding_rerank", "qdrant_hybrid")


class MatrixRunner:
    def __init__(
        self,
        store: KnowledgeStore,
        *,
        embedding=None,
        rerank=None,
        vector_store=None,
        queries: list[EvaluationQuery] | None = None,
    ) -> None:
        self._store = store
        self._embedding = embedding
        self._rerank = rerank
        self._vector_store = vector_store
        self._queries = queries or []

    def run(
        self,
        models: list[ModelProfile],
        *,
        target_ref: str,
        node_type: str,
    ) -> MatrixReport:
        rows: list[MatrixRow] = []
        for model in models:
            for level in RAG_LEVELS:
                embedding = self._embedding if level != "lexical" else None
                rerank = self._rerank if level == "embedding_rerank" else None
                vector_store = (
                    self._vector_store
                    if level == "qdrant_hybrid"
                    else None
                )
                engine = RetrievalEngine(
                    self._store,
                    embedding=embedding,
                    rerank=rerank,
                    vector_store=vector_store,
                )
                evaluation = RetrievalEvaluator(engine).evaluate(
                    self._queries,
                    target_ref=target_ref,
                    node_type=node_type,
                    level=level,
                )
                rows.append(
                    MatrixRow(
                        model=model.name,
                        rag_level=level,
                        hit_rate=evaluation.hit_rate,
                        p95_ms=evaluation.p95_latency_ms,
                        degraded=evaluation.degraded_count,
                        token_estimate=evaluation.token_estimate,
                    )
                )
        return MatrixReport(rows=tuple(rows))
