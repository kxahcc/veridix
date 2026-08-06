from __future__ import annotations

from services.agent_runtime.context_projector import (
    ContextProjector,
    ContextRequest,
)
from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.mcp_connector import ToolPreview
from services.knowledge_service.models import (
    FactRecord,
    KnowledgeChunk,
)
from services.knowledge_service.retrieval import (
    LocalEmbeddingAdapter,
    RetrievalEngine,
    UnavailableEmbeddingAdapter,
)
from services.knowledge_service.skills import SkillRegistry
from services.knowledge_service.skill_retrieval import SkillRetriever
from services.knowledge_service.sqlite_memory import SqliteProjectMemory


class FakeMcpConnector:
    def __init__(self, tools: list[ToolPreview]) -> None:
        self._tools = tools

    def list_tools(self, timeout: float = 10.0) -> list[ToolPreview]:
        return self._tools


def _seed() -> tuple[
    KnowledgeStore,
    SqliteProjectMemory,
    SkillRegistry,
]:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="docs/admin",
            source_ref="docs/admin",
            content="admin panel accepts default credentials",
            trust="project_trusted",
            project_id="project_1",
            subjects=("web", "web_discovery"),
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="external/blog",
            source_ref="external/blog",
            content="admin panel accepts default credentials",
            trust="retrieved_untrusted",
            project_id="project_1",
            subjects=("web", "web_discovery"),
        )
    )
    memory = SqliteProjectMemory(":memory:", "project_1")
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
    skills = SkillRegistry()
    skills.load_builtin(
        [
            {
                "name": "web.discovery",
                "version": "0.1.0",
                "trigger": ["web_discovery"],
                "required_tools": ["browser.open", "proxy.list"],
                "required_runner": "browser",
            },
            {
                "name": "host.enumeration",
                "version": "0.1.0",
                "trigger": ["host"],
                "required_tools": ["nmap.scan"],
                "required_runner": "container",
            },
        ]
    )
    return store, memory, skills


def _request(**overrides) -> ContextRequest:
    base = {
        "project_id": "project_1",
        "mission": "verify default credential exposure on /admin",
        "target_ref": "https://lab.example.test",
        "node_type": "web_discovery",
        "allowed_tools": ("browser.open", "proxy.list"),
        "runner": "browser",
        "knowledge_query": "admin panel default credentials",
        "knowledge_token_budget": 2000,
    }
    base.update(overrides)
    return ContextRequest(**base)


def test_projector_builds_real_context_projection() -> None:
    store, memory, skills = _seed()
    mcp = FakeMcpConnector(
        [
            ToolPreview(
                name="proxy.list",
                description="list proxy observations",
                input_schema={"type": "object"},
            ),
            ToolPreview(
                name="untrusted.extra",
                description="not projected",
                input_schema={"type": "object"},
            ),
        ]
    )
    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(store),
        memory=memory,
        skill_registry=skills,
        mcp_factory=lambda config: mcp,
    )

    projection = projector.project(
        _request(
            mcp_config={"kind": "local", "command": ["mock"]},
        )
    )

    assert projection.knowledge_refs == ("docs/admin",)
    assert projection.retrieval is not None
    assert projection.retrieval.degraded is False
    statuses = {
        view.fact.fact_id: view.status
        for view in projection.memory_views
    }
    assert statuses["f1"] == "conflict"
    assert statuses["f3"] == "stale"
    assert projection.memory_snapshot is not None
    assert projection.memory_snapshot.conflict == 2
    assert projection.memory_digest
    assert projection.included_skill_names == ("web.discovery",)


def test_skill_retriever_scores_reach_projection_and_event(tmp_path) -> None:
    store, memory, skills = _seed()
    retriever = SkillRetriever(
        registry=skills,
        index_path=tmp_path / "skill-vectors.db",
    )
    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(store),
        memory=memory,
        memory_embedding=LocalEmbeddingAdapter(),
        skill_registry=skills,
        skill_retriever=retriever,
        mcp_factory=lambda _config: FakeMcpConnector([]),
    )

    projection = projector.project(_request())

    assert projection.skills.included
    assert projection.skills.scores
    assert "bm25" in projection.skills.channels
    payload = projection.as_event_payload()
    included = payload["skills"]["included"]
    assert included[0]["name"] == "web.discovery"
    assert included[0]["score"] is not None
    assert payload["skills"]["channels"]
    assert any(
        item.get("name") == "host.enumeration"
        and (
            item.get("reason") == "profile_not_matched"
            or "required_tools_missing" in str(item.get("reason", ""))
        )
        for item in projection.skills.omitted
    )
    assert projection.context_digest


def test_projector_reports_rag_degradation() -> None:
    store, memory, skills = _seed()
    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(
            store,
            embedding=UnavailableEmbeddingAdapter(),
        ),
        memory=memory,
        skill_registry=skills,
    )

    projection = projector.project(
        _request(retrieval_level="embedding")
    )

    assert projection.retrieval is not None
    assert projection.retrieval.degraded is True
    assert projection.retrieval.level == "lexical"
    assert projection.rag_degraded == (
        "rag_degraded:embedding_unavailable",
    )


def test_projector_filters_skills_by_loop_allowlist() -> None:
    store, memory, skills = _seed()
    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(store),
        memory=memory,
        skill_registry=skills,
    )

    projection = projector.project(
        _request(allowed_skills=("host.enumeration",))
    )

    assert projection.included_skill_names == ()
    reasons = {
        item["name"]: item["reason"]
        for item in projection.skills.omitted
    }
    assert reasons["web.discovery"] == "skill_not_in_loop_scope"
    assert projection.allowed_skills == ("host.enumeration",)
    assert projection.as_event_payload()["request"]["allowed_skills"] == [
        "host.enumeration",
    ]


def test_projector_records_loop_knowledge_query_for_audit() -> None:
    store, memory, skills = _seed()
    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(store),
        memory=memory,
        skill_registry=skills,
    )

    projection = projector.project(_request())

    assert projection.knowledge_query == (
        "admin panel default credentials"
    )
    payload = projection.as_event_payload()
    assert payload["request"]["knowledge_query"] == (
        "admin panel default credentials"
    )
    assert payload["knowledge"]["included"][0]["chunk_id"] == "docs/admin"


def test_projector_omits_over_budget_and_handles_mcp_failure() -> None:
    store, memory, skills = _seed()
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="long",
            source_ref="docs/long",
            content="word " * 600,
            project_id="project_1",
            subjects=("web_discovery",),
        )
    )

    def failing_mcp(config):
        raise RuntimeError("mcp server crashed")

    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(store),
        memory=memory,
        skill_registry=skills,
        mcp_factory=failing_mcp,
    )

    projection = projector.project(
        _request(
            knowledge_query="word",
            knowledge_token_budget=120,
            mcp_config={"name": "broken", "kind": "local"},
        )
    )

    assert any(
        item.get("kind") == "knowledge"
        and item.get("reason") == "token_budget_exceeded"
        for item in projection.omitted
    )
    assert any(
        item.get("kind") == "mcp"
        and item.get("reason") == "mcp_discovery_failed:RuntimeError"
        for item in projection.omitted
    )


def test_context_digest_changes_when_memory_changes() -> None:
    store, memory, skills = _seed()
    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(store),
        memory=memory,
        skill_registry=skills,
    )
    request = _request()

    first = projector.project(request)
    memory.record("/admin", "accepts_role", "owner")
    second = projector.project(request)

    assert first.context_digest != second.context_digest


def test_projector_retrieval_is_scoped_to_project() -> None:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c_project_1",
            source_ref="docs/a",
            content="admin panel default credentials",
            project_id="project_1",
            trust="project_trusted",
            subjects=("web", "web_discovery"),
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c_project_2",
            source_ref="docs/b",
            content="admin panel default credentials",
            project_id="project_2",
            trust="project_trusted",
            subjects=("web", "web_discovery"),
        )
    )
    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(store),
    )

    projection = projector.project(_request(project_id="project_1"))

    assert [
        chunk.chunk_id
        for chunk in projection.knowledge.chunks
    ] == ["c_project_1"]


def test_projector_includes_cross_session_memory_summaries() -> None:
    store, memory, skills = _seed()
    memory.append_summary(
        "run prev: tools=nikto; findings=Exposure",
        source_ref="run_prev",
    )
    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(store),
        memory=memory,
        skill_registry=skills,
    )

    projection = projector.project(_request())

    assert len(projection.memory_summaries) == 1
    assert projection.memory_summaries[0]["source_ref"] == "run_prev"
    assert "nikto" in projection.memory_summaries[0]["summary"]


def test_memory_retrieval_filters_by_relevance_and_budget() -> None:
    store, memory, skills = _seed()
    for index in range(8):
        memory.append(
            FactRecord(
                fact_id=f"extra_{index}",
                subject="/admin",
                predicate="accepts_role",
                value=f"role_{index}",
                observed_at=f"2026-08-0{1 + index // 5}T00:00:00Z",
            )
        )
    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(store),
        memory=memory,
        skill_registry=skills,
        memory_embedding=LocalEmbeddingAdapter(),
    )

    projection = projector.project(
        _request(
            knowledge_query="admin accepts_role",
            memory_token_budget=20,
            memory_limit=20,
        )
    )

    assert len(projection.memory_views) <= 20
    assert any(
        item.get("kind") == "memory"
        and item.get("reason") == "memory_token_budget_exceeded"
        for item in projection.omitted
    )


def test_memory_retrieval_falls_back_when_embedding_unavailable() -> None:
    store, memory, skills = _seed()
    projector = ContextProjector(
        knowledge_store=store,
        retrieval_engine=RetrievalEngine(store),
        memory=memory,
        skill_registry=skills,
        memory_embedding=UnavailableEmbeddingAdapter(),
    )

    projection = projector.project(
        _request(
            knowledge_query="admin accepts_role",
            memory_token_budget=2000,
            memory_limit=20,
        )
    )

    assert any(
        view.fact.subject == "/admin" for view in projection.memory_views
    )
