from __future__ import annotations

import sqlite3

from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.memory import ProjectMemory
from services.knowledge_service.models import (
    FactRecord,
    KnowledgeChunk,
)
from services.knowledge_service.projection import (
    build_knowledge_view,
    build_skill_projection,
)
from services.knowledge_service.retrieval import (
    LocalEmbeddingAdapter,
    RetrievalEngine,
    UnavailableEmbeddingAdapter,
)
from services.knowledge_service.skills import SkillRegistry


def test_fts_search_returns_cited_chunks_with_trust_filter() -> None:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c1",
            source_ref="docs/admin",
            content="admin panel accepts default credentials",
            trust="project_trusted",
            subjects=("web",),
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c2",
            source_ref="external/blog",
            content="admin panel accepts default credentials",
            trust="retrieved_untrusted",
            subjects=("web",),
        )
    )

    chunks, excluded = store.search(
        "admin panel",
        trust_max="project_trusted",
        subject="web",
    )

    assert [chunk.chunk_id for chunk in chunks] == ["c1"]
    assert chunks[0].source_ref == "docs/admin"
    assert excluded == 0


def test_knowledge_store_migrates_legacy_fts_without_target_refs(
    tmp_path,
) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE VIRTUAL TABLE knowledge_fts USING fts5(
            content,
            search_tokens,
            chunk_id UNINDEXED,
            source_ref UNINDEXED,
            trust UNINDEXED,
            subjects UNINDEXED,
            version UNINDEXED,
            observed_at UNINDEXED,
            expires_at UNINDEXED
        );
        """
    )
    conn.execute(
        """
        INSERT INTO knowledge_fts (
            chunk_id, source_ref, trust, subjects, version,
            observed_at, expires_at, content, search_tokens
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy",
            "docs/legacy",
            "project_trusted",
            "web",
            "1",
            "2026-08-01T00:00:00Z",
            "",
            "legacy knowledge content",
            "legacy knowledge content",
        ),
    )
    conn.commit()
    conn.close()

    store = KnowledgeStore(db)
    chunks = store.list_chunks()

    assert len(chunks) == 1
    assert chunks[0].chunk_id == "legacy"
    assert chunks[0].target_refs == ()

    store.add_chunk(
        KnowledgeChunk(
            chunk_id="scoped",
            source_ref="docs/scoped",
            content="scoped knowledge content",
            target_refs=("https://lab.example.test",),
        )
    )
    assert any(
        chunk.chunk_id == "scoped"
        and chunk.target_refs == ("https://lab.example.test",)
        for chunk in store.list_chunks()
    )


def test_fts_search_orders_by_relevance_and_keeps_trust_filter() -> None:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="best",
            source_ref="docs/hw",
            content="边界突破 目录爆破 漏洞利用",
            trust="project_trusted",
            subjects=("hw",),
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="weak",
            source_ref="docs/generic",
            content="边界突破流程记录",
            trust="project_trusted",
            subjects=("hw",),
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="untrusted",
            source_ref="docs/blog",
            content="边界突破 目录爆破 漏洞利用",
            trust="retrieved_untrusted",
            subjects=("hw",),
        )
    )

    chunks, excluded = store.search(
        "边界突破 目录爆破 漏洞利用",
        trust_max="project_trusted",
        limit=5,
    )

    assert [chunk.chunk_id for chunk in chunks] == ["best", "weak"]
    assert excluded == 0


def test_knowledge_project_scoping_filters_list_and_search() -> None:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c_project_1",
            source_ref="docs/a",
            content="project one web knowledge",
            project_id="project_1",
            subjects=("web",),
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c_project_2",
            source_ref="docs/b",
            content="project two web knowledge",
            project_id="project_2",
            subjects=("web",),
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c_global",
            source_ref="docs/global",
            content="global web knowledge",
            project_id="",
            subjects=("web",),
        )
    )

    assert {
        chunk.chunk_id
        for chunk in store.list_chunks(project_id="project_1")
    } == {"c_project_1", "c_global"}
    assert len(store.list_chunks()) == 3
    chunks, _ = store.search(
        "web",
        project_id="project_2",
    )
    assert {
        chunk.chunk_id
        for chunk in chunks
    } == {"c_project_2", "c_global"}
    project_chunk = next(
        chunk
        for chunk in store.list_chunks(project_id="project_1")
        if chunk.chunk_id == "c_project_1"
    )
    assert project_chunk.project_id == "project_1"


def test_memory_conflict_ttl_and_retract() -> None:
    memory = ProjectMemory("project_1")
    memory.append(
        FactRecord(
            fact_id="f1",
            subject="/admin",
            predicate="accepts_role",
            value="user",
            observed_at="2026-08-01T00:00:00Z",
        )
    )
    memory.append(
        FactRecord(
            fact_id="f2",
            subject="/admin",
            predicate="accepts_role",
            value="admin",
            observed_at="2026-08-01T01:00:00Z",
        )
    )
    memory.append(
        FactRecord(
            fact_id="f3",
            subject="/old",
            predicate="reachable",
            value="true",
            observed_at="2026-07-01T00:00:00Z",
            expires_at="2026-07-02T00:00:00Z",
        )
    )

    statuses = {view.fact.fact_id: view.status for view in memory.projection()}
    assert statuses["f1"] == "conflict"
    assert statuses["f2"] == "conflict"
    assert statuses["f3"] == "stale"

    memory.retract("f1")
    assert len(memory.replay()) == 4


def test_skill_registry_and_node_projection() -> None:
    registry = SkillRegistry()
    registry.load_builtin(
        [
            {
                "name": "web-discovery",
                "version": "0.1.0",
                "trigger": ["web_discovery"],
                "required_tools": ["proxy.list"],
                "required_runner": "web",
            },
            {
                "name": "verifier",
                "version": "0.1.0",
                "trigger": ["verifier"],
                "required_tools": ["evidence.replay"],
                "required_runner": "web",
            },
        ]
    )

    discovery = registry.get("web-discovery")
    assert discovery is not None
    projection = build_skill_projection(
        node_type="web_discovery",
        skills=[discovery],
        allowed_tools=("proxy.list",),
        runner="web",
        registry=registry,
    )
    assert projection.included[0].name == "web-discovery"
    assert projection.omitted == ()

    verifier = registry.get("verifier")
    denied = build_skill_projection(
        node_type="verifier",
        skills=[verifier],
        allowed_tools=("shell.probe",),
        runner="web",
        registry=registry,
    )
    assert denied.included == ()
    assert denied.omitted[0]["reason"].startswith("required_tools_missing")


def test_knowledge_view_respects_token_budget() -> None:
    chunks = [
        KnowledgeChunk(chunk_id=f"c{i}", source_ref=f"src{i}", content="word " * 500)
        for i in range(3)
    ]

    view = build_knowledge_view(
        node_type="web_discovery",
        chunks=chunks,
        token_budget=2000,
    )

    assert len(view.chunks) == 2
    assert view.omitted[0]["reason"] == "token_budget_exceeded"


def test_retrieval_degrades_to_lexical_when_embedding_unavailable() -> None:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c1",
            source_ref="docs/admin",
            content="admin panel default credentials",
        )
    )
    engine = RetrievalEngine(store, embedding=UnavailableEmbeddingAdapter())

    result = engine.retrieve(
        "admin",
        target_ref="https://lab.example.test",
        node_type="web_discovery",
        level="embedding",
    )

    assert result.degraded is True
    assert result.reason == "rag_degraded:embedding_unavailable"
    assert result.level == "lexical"
    assert result.citations == ("docs/admin",)


def test_local_embedding_adapter_reorders_results() -> None:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        KnowledgeChunk(chunk_id="far", source_ref="a", content="other topic")
    )
    store.add_chunk(
        KnowledgeChunk(chunk_id="near", source_ref="b", content="admin token leak")
    )
    engine = RetrievalEngine(store, embedding=LocalEmbeddingAdapter())

    result = engine.retrieve(
        "admin token",
        target_ref="t",
        node_type="web_discovery",
        level="embedding",
    )

    assert result.degraded is False
    assert result.level == "embedding"
    assert result.chunks[0].chunk_id == "near"
