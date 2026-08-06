from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlparse

from .knowledge_store import KnowledgeStore
from .models import KnowledgeChunk


class EmbeddingAdapter(ABC):
    @abstractmethod
    def search(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
    ) -> list[tuple[str, float]]:
        raise NotImplementedError


class UnavailableEmbeddingAdapter(EmbeddingAdapter):
    def search(self, query: str, chunks: list[KnowledgeChunk]):
        raise RuntimeError("embedding provider unavailable")


class LocalEmbeddingAdapter(EmbeddingAdapter):
    """Deterministic fixture adapter; token overlap as pseudo-score."""

    def search(self, query: str, chunks: list[KnowledgeChunk]) -> list[tuple[str, float]]:
        tokens = set(query.lower().split())
        scored = []
        for chunk in chunks:
            overlap = len(tokens & set(chunk.content.lower().split()))
            scored.append((chunk.chunk_id, float(overlap)))
        return sorted(scored, key=lambda item: item[1], reverse=True)


@dataclass(frozen=True)
class RetrievalResult:
    chunks: tuple[KnowledgeChunk, ...]
    citations: tuple[str, ...]
    level: str
    degraded: bool
    reason: str = ""
    excluded: int = 0
    channels: tuple[str, ...] = ("lexical",)
    graph_paths: tuple[str, ...] = ()


class RetrievalEngine:
    RRF_K = 60

    def __init__(
        self,
        store: KnowledgeStore,
        embedding: EmbeddingAdapter | None = None,
        rerank=None,
        vector_store=None,
        graph_store=None,
        deadline_seconds: float = 8.0,
        fusion: str = "rrf",
        min_vector_score: float = 0.0,
        project_id: str | None = None,
    ) -> None:
        self._store = store
        self._embedding = embedding
        self._rerank = rerank
        self._vector_store = vector_store
        self._graph_store = graph_store
        self._deadline_seconds = deadline_seconds
        self._fusion = fusion
        self._min_vector_score = min_vector_score
        self._project_id = project_id

    def retrieve(
        self,
        query: str,
        *,
        target_ref: str,
        node_type: str,
        trust_max: str = "project_trusted",
        limit: int = 5,
        level: str = "lexical",
        subject: str | None = None,
        project_id: str | None = None,
        observed_since: str | None = None,
        observed_until: str | None = None,
    ) -> RetrievalResult:
        if level in ("hybrid", "qdrant_hybrid"):
            return self._retrieve_hybrid(
                query,
                target_ref=target_ref,
                node_type=node_type,
                trust_max=trust_max,
                limit=limit,
                subject=subject,
                project_id=project_id,
                observed_since=observed_since,
                observed_until=observed_until,
            )
        chunks, excluded = self._store.search(
            query,
            trust_max=trust_max,
            limit=limit,
            subject=subject,
            project_id=project_id,
            target_ref=target_ref,
            observed_since=observed_since,
            observed_until=observed_until,
        )
        if level.startswith("embedding"):
            if self._embedding is None:
                return RetrievalResult(
                    chunks=tuple(chunks),
                    citations=tuple(chunk.source_ref for chunk in chunks),
                    level="lexical",
                    degraded=True,
                    reason="rag_degraded:embedding_unavailable",
                    excluded=excluded,
                    channels=("bm25",),
                )
            try:
                ranked = self._embedding.search(query, chunks)
                ids = {chunk_id for chunk_id, _ in ranked}
                chunks = [
                    chunk
                    for chunk in chunks
                    if chunk.chunk_id in ids
                ]
                chunks.sort(
                    key=lambda chunk: next(
                        (score for cid, score in ranked if cid == chunk.chunk_id),
                        0,
                    ),
                    reverse=True,
                )
                if self._rerank is not None:
                    ranked = self._rerank.rerank(query, chunks)
                    ids = {chunk_id for chunk_id, _ in ranked}
                    chunks = [chunk for chunk in chunks if chunk.chunk_id in ids]
                    chunks.sort(
                        key=lambda chunk: next(
                            (score for cid, score in ranked if cid == chunk.chunk_id),
                            0,
                        ),
                        reverse=True,
                    )
                return RetrievalResult(
                    chunks=tuple(chunks),
                    citations=tuple(chunk.source_ref for chunk in chunks),
                    level="embedding",
                    degraded=False,
                    excluded=excluded,
                    channels=("embedding",),
                )
            except Exception:
                return RetrievalResult(
                    chunks=tuple(chunks),
                    citations=tuple(chunk.source_ref for chunk in chunks),
                    level="lexical",
                    degraded=True,
                    reason="rag_degraded:embedding_unavailable",
                    excluded=excluded,
                    channels=("bm25",),
                )
        return RetrievalResult(
            chunks=tuple(chunks),
            citations=tuple(chunk.source_ref for chunk in chunks),
            level="lexical",
            degraded=False,
            excluded=excluded,
            channels=("bm25",),
        )

    def _retrieve_hybrid(
        self,
        query: str,
        *,
        target_ref: str,
        node_type: str,
        trust_max: str,
        limit: int,
        subject: str | None,
        project_id: str | None,
        observed_since: str | None,
        observed_until: str | None,
    ) -> RetrievalResult:
        started = time.time()
        lexical_chunks, excluded = self._store.search(
            query,
            trust_max=trust_max,
            limit=20,
            subject=subject,
            project_id=project_id,
            target_ref=target_ref,
            observed_since=observed_since,
            observed_until=observed_until,
        )
        channels: list[str] = ["bm25"]
        degraded: list[str] = []
        scored: dict[str, float] = {
            chunk.chunk_id: self._rrf_score(index)
            for index, chunk in enumerate(lexical_chunks)
        }
        chunks_by_id = {
            chunk.chunk_id: chunk for chunk in lexical_chunks
        }

        vector_state = {"hits": [], "ok": False, "reason": ""}
        graph_state = {"chunk_ids": set(), "paths": [], "ok": False}

        vector_thread = threading.Thread(
            target=self._run_vector_channel,
            args=(query, vector_state, self._project_id),
            daemon=True,
        )
        graph_thread = threading.Thread(
            target=self._run_graph_channel,
            args=(query, graph_state),
            daemon=True,
        )
        vector_thread.start()
        graph_thread.start()

        vector_deadline = self._remaining(started)
        graph_deadline = self._remaining(started)
        vector_thread.join(max(0.0, vector_deadline))
        graph_thread.join(max(0.0, graph_deadline))
        if vector_thread.is_alive():
            degraded.append("rag_degraded:vector_store_timeout")
        else:
            vector_hits = vector_state["hits"]
            if vector_state["ok"] and vector_hits:
                channels.append("vector")
            elif vector_state["reason"]:
                degraded.append(vector_state["reason"])

        if graph_thread.is_alive():
            degraded.append("rag_degraded:graph_timeout")
        else:
            graph_chunk_ids = graph_state["chunk_ids"]
            graph_paths = graph_state["paths"]
            if graph_chunk_ids or graph_paths:
                channels.append("graph")

        if self._fusion == "weighted":
            for chunk_id, score in vector_state["hits"]:
                scored[chunk_id] = (
                    scored.get(chunk_id, 0.0) + 0.5 * float(score)
                )
            for chunk_id in graph_state["chunk_ids"]:
                scored[chunk_id] = scored.get(chunk_id, 0.0) + 1.0
        elif self._fusion == "vector_first":
            for chunk_id, score in vector_state["hits"]:
                scored[chunk_id] = (
                    scored.get(chunk_id, 0.0) + float(score)
                )
            for chunk_id in graph_state["chunk_ids"]:
                scored[chunk_id] = scored.get(chunk_id, 0.0) + 0.5
        else:
            for index, (chunk_id, _score) in enumerate(
                vector_state["hits"]
            ):
                scored[chunk_id] = scored.get(chunk_id, 0.0) + self._rrf_score(
                    index
                )
            graph_boost = self._rrf_score(0)
            for chunk_id in graph_state["chunk_ids"]:
                scored[chunk_id] = scored.get(chunk_id, 0.0) + graph_boost

        all_ids = list(scored)
        missing = [chunk_id for chunk_id in all_ids if chunk_id not in chunks_by_id]
        for chunk in self._store.chunks_by_id(tuple(missing)):
            chunks_by_id[chunk.chunk_id] = chunk

        scoped_ids = {
            chunk_id
            for chunk_id, chunk in chunks_by_id.items()
            if _scope_match(
                chunk,
                target_ref=target_ref,
                observed_since=observed_since,
                observed_until=observed_until,
            )
        }
        scored = {
            chunk_id: score
            for chunk_id, score in scored.items()
            if chunk_id in scoped_ids
        }
        vector_state["hits"] = [
            (chunk_id, score)
            for chunk_id, score in vector_state["hits"]
            if chunk_id in scoped_ids
        ]
        graph_state["chunk_ids"] = {
            chunk_id
            for chunk_id in graph_state["chunk_ids"]
            if chunk_id in scoped_ids
        }

        ranked_ids = [
            chunk_id
            for chunk_id, _ in sorted(
                scored.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        ][:20]
        chunks = [
            chunks_by_id[chunk_id]
            for chunk_id in ranked_ids
            if chunk_id in chunks_by_id
        ]

        if vector_state["ok"] and vector_state["hits"]:
            recall_floor = max(1, limit // 2)
            vector_order = [
                chunk_id
                for chunk_id, _score in vector_state["hits"]
                if chunk_id in chunks_by_id
            ]
            vector_rank = {
                chunk_id: index
                for index, chunk_id in enumerate(vector_order)
            }
            final_ids = [chunk.chunk_id for chunk in chunks[:limit]]
            semantic_priority = vector_order[:recall_floor]
            for semantic_id in semantic_priority:
                if semantic_id in final_ids:
                    continue
                candidates = [
                    index
                    for index, chunk_id in enumerate(final_ids)
                    if chunk_id not in semantic_priority
                ]
                if not candidates:
                    break
                worst = max(
                    candidates,
                    key=lambda index: vector_rank.get(
                        final_ids[index],
                        10**9,
                    ),
                )
                final_ids[worst] = semantic_id
            chunks = [
                chunks_by_id[chunk_id]
                for chunk_id in final_ids
                if chunk_id in chunks_by_id
            ]

        if self._rerank is not None:
            try:
                rerank_deadline = self._remaining(started)
                if rerank_deadline > 0.25:
                    ranked = self._rerank.rerank(query, chunks[:10])
                else:
                    ranked = []
                if ranked:
                    ids = {chunk_id for chunk_id, _ in ranked}
                    chunks = [
                        chunk
                        for chunk in chunks
                        if chunk.chunk_id in ids
                    ]
                    chunks.sort(
                        key=lambda chunk: next(
                            (
                                score
                                for cid, score in ranked
                                if cid == chunk.chunk_id
                            ),
                            0.0,
                        ),
                        reverse=True,
                    )
                    channels.append("rerank")
                else:
                    degraded.append("rag_degraded:rerank_timeout")
            except Exception:
                degraded.append("rag_degraded:rerank_unavailable")

        return RetrievalResult(
            chunks=tuple(chunks[:limit]),
            citations=tuple(chunk.source_ref for chunk in chunks[:limit]),
            level="hybrid",
            degraded=bool(degraded),
            reason=";".join(degraded) if degraded else "",
            excluded=excluded,
            channels=tuple(channels),
            graph_paths=tuple(sorted(set(graph_state["paths"]))),
        )

    def _rrf_score(self, rank: int) -> float:
        return 1.0 / (self.RRF_K + rank + 1)

    def _remaining(self, started: float) -> float:
        return self._deadline_seconds - (time.time() - started)

    def _run_vector_channel(
        self,
        query: str,
        state: dict,
        project_id: str | None = None,
    ) -> None:
        if self._vector_store is None:
            return
        embed_query = getattr(self._embedding, "embed_query", None)
        if embed_query is None:
            state["reason"] = "rag_degraded:vector_store_without_embed_query"
            return
        try:
            query_vector = embed_query(query)
            if project_id and hasattr(
                self._vector_store,
                "_project_filter",
            ):
                hits = self._vector_store.search(
                    query_vector,
                    limit=20,
                    project_id=project_id,
                )
            else:
                hits = self._vector_store.search(
                    query_vector,
                    limit=20,
                )
            hits = [
                (chunk_id, score)
                for chunk_id, score in hits
                if score >= self._min_vector_score
            ]
            state["hits"] = list(hits)
            state["ok"] = True
            if hasattr(self._vector_store, "search_hybrid"):
                from .sparse_encoder import sparse_encode

                sparse = sparse_encode(query)
                try:
                    if project_id and hasattr(
                        self._vector_store,
                        "_project_filter",
                    ):
                        hits = self._vector_store.search_hybrid(
                            query_vector,
                            indices=sparse["indices"],
                            values=sparse["values"],
                            limit=20,
                            project_id=project_id,
                        )
                    else:
                        hits = self._vector_store.search_hybrid(
                            query_vector,
                            indices=sparse["indices"],
                            values=sparse["values"],
                            limit=20,
                        )
                    state["hits"] = [
                        (chunk_id, score)
                        for chunk_id, score in hits
                        if score >= self._min_vector_score
                    ]
                    state["ok"] = True
                    state["mode"] = "qdrant_sparse_dense"
                    return
                except Exception:
                    pass
        except Exception:
            state["reason"] = "rag_degraded:vector_store_unavailable"

    def _run_graph_channel(self, query: str, state: dict) -> None:
        if self._graph_store is None:
            return
        try:
            terms = tuple(
                sorted(
                    {
                        token.lower()
                        for token in _query_terms(query)
                        if len(token) >= 3
                    }
                )
            )
            nodes = self._graph_store.nodes_for_terms(terms)
            expanded = self._graph_store.neighbors(tuple(nodes), depth=1)
            linked = self._graph_store.chunk_ids_for_nodes(tuple(expanded))
            labels = self._graph_store.path_labels(tuple(expanded))
            chunk_ids: set[str] = set()
            for chunk_ids_for_node in linked.values():
                chunk_ids.update(chunk_ids_for_node)
            state["chunk_ids"] = chunk_ids
            state["paths"] = [
                labels.get(node_id, node_id) for node_id in expanded
            ]
            state["ok"] = True
        except Exception:
            state["reason"] = "rag_degraded:graph_unavailable"


def _query_terms(query: str) -> list[str]:
    from .sparse_encoder import tokenize

    return [term for term in tokenize(query) if len(term) >= 2]


def _scope_match(
    chunk,
    *,
    target_ref: str | None,
    observed_since: str | None,
    observed_until: str | None,
) -> bool:
    if observed_since and chunk.observed_at and chunk.observed_at < observed_since:
        return False
    if observed_until and chunk.observed_at and chunk.observed_at > observed_until:
        return False
    if not target_ref or not chunk.target_refs:
        return True
    return any(
        _target_ref_matches(scope_ref, target_ref)
        for scope_ref in chunk.target_refs
    )


def _target_ref_matches(scope_ref: str, target_ref: str) -> bool:
    if scope_ref == target_ref:
        return True
    scope_host = urlparse(scope_ref).hostname or scope_ref
    target_host = urlparse(target_ref).hostname or target_ref
    if not scope_host or not target_host:
        return scope_ref in target_ref or target_ref in scope_ref
    return (
        scope_host == target_host
        or scope_host.endswith(f".{target_host}")
        or target_host.endswith(f".{scope_host}")
    )
