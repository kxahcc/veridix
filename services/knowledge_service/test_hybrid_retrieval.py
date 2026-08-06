from __future__ import annotations

from services.knowledge_service.graph_store import KnowledgeGraphStore
from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.models import KnowledgeChunk
from services.knowledge_service.retrieval import RetrievalEngine, _query_terms


def _chunk(
    chunk_id: str,
    content: str,
    *,
    graph: dict | None = None,
    target_refs: tuple[str, ...] = (),
    observed_at: str = "2026-08-01T00:00:00Z",
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        source_ref=f"builtin/{chunk_id}",
        content=content,
        trust="project_trusted",
        subjects=("web_test",),
        graph=graph,
        target_refs=target_refs,
        observed_at=observed_at,
    )


def _seed() -> tuple[KnowledgeStore, KnowledgeGraphStore]:
    store = KnowledgeStore(":memory:")
    graph = KnowledgeGraphStore(":memory:")
    sqli = _chunk(
        "vuln.sqli",
        "test SQL injection with response diff and sqlmap structured output",
        graph={
            "nodes": [
                {
                    "id": "vuln.sqli",
                    "type": "vuln_class",
                    "label": "SQL Injection",
                },
                {
                    "id": "tech.sqlmap",
                    "type": "tool",
                    "label": "sqlmap",
                },
            ],
            "edges": [
                {
                    "source": "vuln.sqli",
                    "target": "tech.sqlmap",
                    "predicate": "tested_with",
                }
            ],
        },
    )
    authz = _chunk(
        "authz.private",
        "zzz qqq unrelated lexical content",
        graph={
            "nodes": [
                {
                    "id": "vuln.authz-matrix",
                    "type": "vuln_class",
                    "label": "Authorization Matrix",
                }
            ]
        },
    )
    store.add_chunk(sqli)
    store.add_chunk(authz)
    graph.register_chunk_graph(sqli)
    graph.register_chunk_graph(authz)
    return store, graph


def test_query_terms_include_cjk_bigrams() -> None:
    terms = _query_terms("路径遍历 越权访问 SQL 注入")

    assert "sql" in terms
    assert "路径" in terms
    assert "越权" in terms


def test_graph_store_registers_links_and_expands_neighbors() -> None:
    store, graph = _seed()

    assert graph.node_count() == 3
    assert graph.edge_count() == 1
    nodes = graph.nodes_for_terms(("authz",))
    assert "vuln.authz-matrix" in nodes
    linked = graph.chunk_ids_for_nodes(tuple(nodes))
    assert "authz.private" in linked.get("vuln.authz-matrix", [])
    neighbors = graph.neighbors(("vuln.sqli",))
    assert "tech.sqlmap" in neighbors


def test_hybrid_retrieval_boosts_graph_linked_chunk() -> None:
    store, graph = _seed()
    engine = RetrievalEngine(
        store,
        graph_store=graph,
    )

    result = engine.retrieve(
        "authz matrix business rule",
        target_ref="https://lab.example.test",
        node_type="web_test",
        level="hybrid",
        limit=5,
    )

    assert "graph" in result.channels
    assert result.graph_paths
    assert any(
        chunk.chunk_id == "authz.private"
        for chunk in result.chunks
    )


def test_hybrid_retrieval_uses_vector_channel_when_available() -> None:
    store, graph = _seed()

    class FakeEmbedding:
        def embed_query(self, query: str) -> list[float]:
            return [0.1, 0.2, 0.3]

    class FakeVectorStore:
        def __init__(self, hits: dict[str, float]) -> None:
            self._hits = hits

        def search(
            self,
            query_vector: list[float],
            limit: int = 5,
        ) -> list[tuple[str, float]]:
            return sorted(
                self._hits.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:limit]

    engine = RetrievalEngine(
        store,
        embedding=FakeEmbedding(),
        vector_store=FakeVectorStore({"vuln.sqli": 0.9}),
        graph_store=graph,
    )

    result = engine.retrieve(
        "sql injection",
        target_ref="https://lab.example.test",
        node_type="web_test",
        level="hybrid",
        limit=5,
    )

    assert "vector" in result.channels
    assert "graph" in result.channels
    assert any(
        chunk.chunk_id == "vuln.sqli"
        for chunk in result.chunks
    )


def test_hybrid_retrieval_keeps_vector_only_hit_in_top_results() -> None:
    store, _ = _seed()

    class FakeEmbedding:
        def embed_query(self, query: str) -> list[float]:
            return [0.1, 0.2, 0.3]

    class FakeVectorStore:
        def search(
            self,
            query_vector: list[float],
            limit: int = 5,
        ) -> list[tuple[str, float]]:
            return [("authz.private", 0.9), ("vuln.sqli", 0.1)]

        def search_hybrid(
            self,
            query_vector: list[float],
            *,
            indices: list[int],
            values: list[float],
            limit: int = 5,
        ) -> list[tuple[str, float]]:
            return self.search(query_vector, limit=limit)

    engine = RetrievalEngine(
        store,
        embedding=FakeEmbedding(),
        vector_store=FakeVectorStore(),
    )

    result = engine.retrieve(
        "business rule enforcement",
        target_ref="https://lab.example.test",
        node_type="web_test",
        level="hybrid",
        limit=5,
    )

    assert any(
        chunk.chunk_id == "authz.private"
        for chunk in result.chunks
    )


def test_hybrid_retrieval_filters_weak_vector_hits() -> None:
    store, _ = _seed()

    class FakeEmbedding:
        def embed_query(self, query: str) -> list[float]:
            return [0.1, 0.2, 0.3]

    class FakeVectorStore:
        def search(
            self,
            query_vector: list[float],
            limit: int = 5,
        ) -> list[tuple[str, float]]:
            return [
                ("vuln.sqli", 0.05),
                ("authz.private", 0.05),
            ]

        def search_hybrid(
            self,
            query_vector: list[float],
            *,
            indices: list[int],
            values: list[float],
            limit: int = 5,
        ) -> list[tuple[str, float]]:
            return self.search(query_vector, limit=limit)

    engine = RetrievalEngine(
        store,
        embedding=FakeEmbedding(),
        vector_store=FakeVectorStore(),
        min_vector_score=0.2,
    )

    result = engine.retrieve(
        "sql injection",
        target_ref="https://lab.example.test",
        node_type="web_test",
        level="hybrid",
        limit=5,
    )

    assert "vector" not in result.channels
    assert all(
        chunk.chunk_id != "authz.private"
        for chunk in result.chunks
    )


def test_hybrid_retrieval_degrades_cleanly() -> None:
    store, _ = _seed()
    engine = RetrievalEngine(store)

    result = engine.retrieve(
        "sql injection",
        target_ref="https://lab.example.test",
        node_type="web_test",
        level="hybrid",
        limit=5,
    )

    assert result.channels == ("bm25",)
    assert result.degraded is False
    assert any(
        chunk.chunk_id == "vuln.sqli"
        for chunk in result.chunks
    )


def test_retrieval_filters_by_target_ref_but_keeps_global_knowledge() -> None:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        _chunk(
            "scoped.lab",
            "sql injection lab marker",
            target_refs=("https://lab.example.test",),
        )
    )
    store.add_chunk(
        _chunk(
            "scoped.other",
            "sql injection other marker",
            target_refs=("https://other.example.test",),
        )
    )
    store.add_chunk(
        _chunk(
            "global.sql",
            "sql injection global marker",
        )
    )
    engine = RetrievalEngine(store)

    result = engine.retrieve(
        "sql injection",
        target_ref="https://lab.example.test",
        node_type="web_test",
        level="lexical",
        limit=10,
    )

    ids = {chunk.chunk_id for chunk in result.chunks}
    assert "scoped.lab" in ids
    assert "global.sql" in ids
    assert "scoped.other" not in ids


def test_retrieval_filters_by_observed_time_window() -> None:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        _chunk(
            "old.scoped",
            "old observed fact marker",
            target_refs=("https://lab.example.test",),
            observed_at="2026-07-01T00:00:00Z",
        )
    )
    store.add_chunk(
        _chunk(
            "new.scoped",
            "new observed fact marker",
            target_refs=("https://lab.example.test",),
            observed_at="2026-08-01T00:00:00Z",
        )
    )
    engine = RetrievalEngine(store)

    result = engine.retrieve(
        "observed fact",
        target_ref="https://lab.example.test",
        node_type="web_test",
        level="lexical",
        observed_since="2026-07-15T00:00:00Z",
        observed_until="2026-08-15T00:00:00Z",
        limit=10,
    )

    assert [chunk.chunk_id for chunk in result.chunks] == ["new.scoped"]
