from __future__ import annotations

import time
from dataclasses import dataclass, field

from .knowledge_store import KnowledgeStore
from .models import KnowledgeChunk


@dataclass(frozen=True)
class EvaluationQuery:
    query: str
    expected: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalEvaluation:
    queries: int
    hit_rate: float
    p95_latency_ms: float
    token_estimate: int
    degraded_count: int


class RetrievalEvaluator:
    def __init__(self, engine) -> None:
        self._engine = engine

    def evaluate(
        self,
        queries: list[EvaluationQuery],
        *,
        target_ref: str,
        node_type: str,
        limit: int = 5,
        level: str = "embedding",
    ) -> RetrievalEvaluation:
        hits = 0
        latencies: list[float] = []
        tokens = 0
        degraded = 0
        for item in queries:
            start = time.perf_counter()
            result = self._engine.retrieve(
                item.query,
                target_ref=target_ref,
                node_type=node_type,
                limit=limit,
                level=level,
            )
            latencies.append((time.perf_counter() - start) * 1000)
            returned = {chunk.chunk_id for chunk in result.chunks}
            if any(expected in returned for expected in item.expected):
                hits += 1
            tokens += sum(len(chunk.content.split()) * 2 for chunk in result.chunks)
            if result.degraded:
                degraded += 1
        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0.0
        return RetrievalEvaluation(
            queries=len(queries),
            hit_rate=round(hits / max(1, len(queries)), 3),
            p95_latency_ms=round(p95, 3),
            token_estimate=tokens,
            degraded_count=degraded,
        )
