from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.mcp_connector import (
    ContainerMcpConnector,
    LocalMcpConnector,
    ToolPreview,
    project_tools,
)
from services.knowledge_service.memory import (
    FactView,
    MemorySnapshot,
)
from services.knowledge_service.projection import (
    KnowledgeView,
    SkillProjection,
    build_knowledge_view,
    build_skill_projection,
)
from services.knowledge_service.retrieval import (
    RetrievalEngine,
    RetrievalResult,
)
from services.knowledge_service.models import KnowledgeChunk
from services.knowledge_service.skills import SkillRegistry
from services.knowledge_service.sqlite_memory import SqliteProjectMemory

from .kernel.context_trimming import estimate_tokens
from .kernel.harness import digest


def node_type_for_target(target_ref: str) -> str:
    if target_ref.startswith(("http://", "https://")):
        return "web_discovery"
    return "host"


def runner_for_node_type(node_type: str) -> str:
    return "browser" if node_type == "web_discovery" else "container"


@dataclass(frozen=True)
class ContextRequest:
    project_id: str
    mission: str
    target_ref: str
    node_type: str = "web_discovery"
    allowed_tools: tuple[str, ...] = ()
    allowed_skills: tuple[str, ...] = ()
    runner: str = "browser"
    knowledge_query: str = ""
    retrieval_level: str = "lexical"
    trust_max: str = "project_trusted"
    knowledge_token_budget: int = 2000
    skill_token_budget: int = 6000
    skill_selection_limit: int = 6
    skill_retrieval_level: str = "hybrid"
    memory_subjects: tuple[str, ...] = ()
    memory_token_budget: int = 2000
    memory_limit: int = 20
    memory_retrieval_level: str = "hybrid"
    mcp_config: dict[str, Any] | None = None
    mcp_timeout: float = 10.0
    observed_since: str | None = None
    observed_until: str | None = None


def _retrieve_memory_views(
    views: tuple[FactView, ...],
    *,
    query: str,
    embedding=None,
    level: str = "hybrid",
    limit: int = 20,
    token_budget: int = 2000,
) -> tuple[tuple[FactView, ...], tuple[dict[str, str], ...]]:
    active = tuple(
        view
        for view in views
        if view.status in ("active", "conflict")
    )
    stale = tuple(
        view
        for view in views
        if view.status not in ("active", "conflict")
    )
    if not active:
        return stale, ()
    chunks = [
        KnowledgeChunk(
            chunk_id=view.fact.fact_id,
            source_ref=view.fact.source_refs[0] if view.fact.source_refs else "",
            content=(
                f"{view.fact.subject} {view.fact.predicate} "
                f"{view.fact.value}"
            ),
            trust=view.fact.trust,
        )
        for view in active
    ]
    scored: list[tuple[int, float]] = []
    try:
        if embedding is not None and level != "lexical":
            ranked = embedding.search(query, chunks)
            score_map = dict(ranked)
            scored = [
                (index, score_map.get(chunk.chunk_id, 0.0))
                for index, chunk in enumerate(chunks)
            ]
        else:
            query_tokens = set(query.lower().split())
            scored = [
                (
                    index,
                    float(
                        len(
                            query_tokens
                            & set(chunk.content.lower().split())
                        )
                    ),
                )
                for index, chunk in enumerate(chunks)
            ]
    except Exception:
        query_tokens = set(query.lower().split())
        scored = [
            (
                index,
                float(
                    len(query_tokens & set(chunk.content.lower().split()))
                ),
            )
            for index, chunk in enumerate(chunks)
        ]
    ranked_indexes = [
        index
        for index, _ in sorted(
            scored,
            key=lambda item: item[1],
            reverse=True,
        )
    ][: max(1, int(limit))]

    kept: list[FactView] = []
    omitted: list[dict[str, str]] = []
    used = 0
    for index in ranked_indexes:
        view = active[index]
        tokens = estimate_tokens(
            f"{view.fact.subject} {view.fact.predicate} {view.fact.value}"
        )
        if kept and used + tokens > token_budget:
            omitted.append(
                {
                    "kind": "memory",
                    "name": view.fact.fact_id,
                    "reason": "memory_token_budget_exceeded",
                }
            )
            continue
        kept.append(view)
        used += tokens
    if len(active) > len(kept):
        omitted.append(
            {
                "kind": "memory",
                "name": "*",
                "reason": "memory_limit_or_score_filtered",
            }
        )
    return tuple((*kept, *stale)), tuple(omitted)


def _filter_allowed_skills(
    projection: SkillProjection,
    allowed_skills: tuple[str, ...],
) -> SkillProjection:
    if not allowed_skills:
        return projection
    allowed = set(allowed_skills)
    kept: list[Any] = []
    scores: list[float] = []
    omitted = list(projection.omitted)
    for index, skill in enumerate(projection.included):
        if skill.name in allowed:
            kept.append(skill)
            if index < len(projection.scores):
                scores.append(projection.scores[index])
        else:
            omitted.append(
                {
                    "name": skill.name,
                    "version": skill.version,
                    "reason": "skill_not_in_loop_scope",
                }
            )
    return SkillProjection(
        node_type=projection.node_type,
        included=tuple(kept),
        omitted=tuple(omitted),
        scores=tuple(scores),
        channels=projection.channels,
    )


@dataclass(frozen=True)
class ContextProjection:
    node_type: str
    target_ref: str
    knowledge: KnowledgeView
    retrieval: RetrievalResult | None
    memory_views: tuple[FactView, ...]
    memory_snapshot: MemorySnapshot | None
    memory_digest: str
    skills: SkillProjection
    mcp_included: tuple[ToolPreview, ...]
    omitted: tuple[dict[str, str], ...]
    token_estimate: int
    rag_degraded: tuple[str, ...]
    context_digest: str
    memory_summaries: tuple[dict, ...] = ()
    knowledge_query: str = ""
    allowed_skills: tuple[str, ...] = ()

    @property
    def included_skill_names(self) -> tuple[str, ...]:
        return tuple(skill.name for skill in self.skills.included)

    @property
    def knowledge_refs(self) -> tuple[str, ...]:
        return tuple(chunk.chunk_id for chunk in self.knowledge.chunks)

    def as_event_payload(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type,
            "target_ref": self.target_ref,
            "knowledge": {
                "included": [
                    {
                        "chunk_id": chunk.chunk_id,
                        "source_ref": chunk.source_ref,
                        "trust": chunk.trust,
                        "version": chunk.version,
                        "subjects": list(chunk.subjects),
                    }
                    for chunk in self.knowledge.chunks
                ],
                "omitted": [
                    {"kind": "knowledge", **dict(item)}
                    for item in self.knowledge.omitted
                ],
                "token_estimate": self.knowledge.token_estimate,
            },
            "retrieval": (
                {
                    "level": self.retrieval.level,
                    "degraded": self.retrieval.degraded,
                    "reason": self.retrieval.reason,
                    "citations": list(self.retrieval.citations),
                    "excluded": self.retrieval.excluded,
                }
                if self.retrieval is not None
                else None
            ),
            "memory": {
                "digest": self.memory_digest,
                "snapshot": (
                    {
                        "total_facts": self.memory_snapshot.total_facts,
                        "active": self.memory_snapshot.active,
                        "conflict": self.memory_snapshot.conflict,
                        "stale": self.memory_snapshot.stale,
                    }
                    if self.memory_snapshot is not None
                    else None
                ),
                "facts": [
                    {
                        "fact_id": view.fact.fact_id,
                        "subject": view.fact.subject,
                        "predicate": view.fact.predicate,
                        "value": view.fact.value,
                        "status": view.status,
                        "trust": view.fact.trust,
                        "expires_at": view.fact.expires_at,
                    }
                    for view in self.memory_views
                ],
            },
            "skills": {
                "included": [
                    {
                        "name": skill.name,
                        "version": skill.version,
                        "score": (
                            self.skills.scores[index]
                            if index < len(self.skills.scores)
                            else None
                        ),
                        "description": skill.description,
                        "category": skill.category,
                        "tags": list(skill.tags),
                        "cwe_ids": list(skill.cwe_ids),
                        "prerequisites": list(skill.prerequisites),
                        "chains_with": list(skill.chains_with),
                        "risk_level": skill.risk_level,
                        "required_tools": list(skill.required_tools),
                        "required_runner": skill.required_runner,
                    }
                    for index, skill in enumerate(self.skills.included)
                ],
                "scores": list(self.skills.scores),
                "channels": list(self.skills.channels),
                "omitted": [
                    {"kind": "skill", **dict(item)}
                    for item in self.skills.omitted
                ],
            },
            "mcp": {
                "included": [
                    {
                        "name": tool.name,
                        "source": tool.source,
                        "trust": tool.trust,
                    }
                    for tool in self.mcp_included
                ],
                "omitted": [
                    dict(item)
                    for item in self.omitted
                    if item.get("kind") == "mcp"
                ],
            },
            "omitted": [dict(item) for item in self.omitted],
            "token_estimate": self.token_estimate,
            "rag_degraded": list(self.rag_degraded),
            "context_digest": self.context_digest,
            "request": {
                "knowledge_query": self.knowledge_query,
                "allowed_skills": list(self.allowed_skills),
            },
        }


def default_mcp_factory(config: dict[str, Any]):
    kind = config.get("kind", "local")
    if kind == "local":
        return LocalMcpConnector(
            list(config["command"]),
            cwd=config.get("cwd"),
        )
    if kind == "container":
        return ContainerMcpConnector(
            list(config["command"]),
            image=config["image"],
            container_name=config["container_name"],
        )
    raise ValueError(f"unsupported mcp kind: {kind}")


class ContextProjector:
    """Node-level projection of knowledge, memory, skills and MCP tools."""

    def __init__(
        self,
        *,
        knowledge_store: KnowledgeStore | None = None,
        retrieval_engine: RetrievalEngine | None = None,
        memory: SqliteProjectMemory | None = None,
        memory_embedding=None,
        skill_registry: SkillRegistry | None = None,
        skill_retriever=None,
        mcp_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._store = knowledge_store
        self._retrieval = retrieval_engine
        self._memory = memory
        self._memory_embedding = memory_embedding
        self._skills = skill_registry
        self._skill_retriever = skill_retriever
        self._mcp_factory = mcp_factory or default_mcp_factory

    @property
    def memory_embedding(self):
        return self._memory_embedding

    def project(self, request: ContextRequest) -> ContextProjection:
        knowledge, retrieval, omitted, degraded = self._project_knowledge(
            request
        )
        (
            memory_views,
            memory_summaries,
            memory_snapshot,
            memory_digest,
            memory_omitted,
        ) = self._project_memory(request)
        skills, skill_omitted, skill_degraded = self._project_skills(request)
        mcp_included, mcp_omitted = self._project_mcp(request)
        omitted = (
            *omitted,
            *memory_omitted,
            *skill_omitted,
            *mcp_omitted,
        )
        degraded = [*degraded, *skill_degraded]
        token_estimate = (
            knowledge.token_estimate
            + sum(
                len(json.dumps(tool.input_schema, sort_keys=True)) // 4
                for tool in mcp_included
            )
            + sum(len(view.fact.value) // 4 for view in memory_views)
            + sum(
                estimate_tokens(skill.content)
                for skill in skills.included
            )
        )
        context_digest = digest(
            [
                request.node_type,
                request.target_ref,
                knowledge.token_estimate,
                tuple(
                    (chunk.chunk_id, chunk.trust, chunk.version)
                    for chunk in knowledge.chunks
                ),
                memory_digest,
                tuple(
                    (skill.name, skill.version)
                    for skill in skills.included
                ),
                tuple(tool.name for tool in mcp_included),
                tuple(omitted),
                tuple(degraded),
            ]
        )
        return ContextProjection(
            node_type=request.node_type,
            target_ref=request.target_ref,
            knowledge=knowledge,
            retrieval=retrieval,
            memory_views=memory_views,
            memory_summaries=memory_summaries,
            memory_snapshot=memory_snapshot,
            memory_digest=memory_digest,
            skills=skills,
            mcp_included=mcp_included,
            omitted=tuple(omitted),
            token_estimate=token_estimate,
            rag_degraded=tuple(degraded),
            context_digest=context_digest,
            knowledge_query=request.knowledge_query,
            allowed_skills=request.allowed_skills,
        )

    def _project_knowledge(
        self,
        request: ContextRequest,
    ) -> tuple[
        KnowledgeView,
        RetrievalResult | None,
        tuple[dict[str, str], ...],
        list[str],
    ]:
        if self._store is None:
            return (
                KnowledgeView(
                    node_type=request.node_type,
                    chunks=(),
                    omitted=(
                        {
                            "chunk_id": "*",
                            "reason": "knowledge_store_unavailable",
                        },
                    ),
                ),
                None,
                (
                    {
                        "kind": "knowledge",
                        "name": "*",
                        "reason": "knowledge_store_unavailable",
                    },
                ),
                [],
            )
        engine = self._retrieval or RetrievalEngine(self._store)
        query = (
            request.knowledge_query
            or request.mission
            or f"{request.node_type} {request.target_ref}"
        )
        result = engine.retrieve(
            query,
            target_ref=request.target_ref,
            node_type=request.node_type,
            trust_max=request.trust_max,
            limit=10,
            level=request.retrieval_level,
            subject=request.node_type,
            project_id=request.project_id,
            observed_since=request.observed_since,
            observed_until=request.observed_until,
        )
        view = build_knowledge_view(
            node_type=request.node_type,
            chunks=list(result.chunks),
            trust_max=request.trust_max,
            token_budget=request.knowledge_token_budget,
        )
        omitted = tuple(
            {
                "kind": "knowledge",
                **item,
            }
            for item in view.omitted
        )
        degraded = (
            [result.reason]
            if result.degraded and result.reason
            else []
        )
        return view, result, omitted, degraded

    def _project_memory(
        self,
        request: ContextRequest,
    ) -> tuple[
        tuple[FactView, ...],
        tuple[dict, ...],
        MemorySnapshot | None,
        str,
        tuple[dict[str, str], ...],
    ]:
        if self._memory is None:
            return (
                (),
                (),
                None,
                digest([]),
                (
                    {
                        "kind": "memory",
                        "name": "project_memory",
                        "reason": "memory_unavailable",
                    },
                ),
            )
        views = self._memory.projection()
        if request.memory_subjects:
            allowed = set(request.memory_subjects)
            views = tuple(
                view
                for view in views
                if view.fact.subject in allowed
            )
        query = (
            request.knowledge_query
            or request.mission
            or f"{request.node_type} {request.target_ref}"
        )
        views, memory_omitted = _retrieve_memory_views(
            views,
            query=query,
            embedding=self._memory_embedding,
            level=request.memory_retrieval_level,
            limit=request.memory_limit,
            token_budget=request.memory_token_budget,
        )
        snapshot = self._memory.snapshot()
        summaries = self._memory.summaries(limit=5)
        memory_digest = digest(
            [
                (
                    view.fact.fact_id,
                    view.fact.subject,
                    view.fact.predicate,
                    view.fact.value,
                    view.status,
                    view.fact.expires_at,
                )
                for view in views
            ]
        )
        return (
            views,
            summaries,
            snapshot,
            memory_digest,
            memory_omitted,
        )

    def _project_skills(
        self,
        request: ContextRequest,
    ) -> tuple[
        SkillProjection,
        tuple[dict[str, str], ...],
        list[str],
    ]:
        if self._skills is None:
            return (
                SkillProjection(
                    node_type=request.node_type,
                    included=(),
                    omitted=(
                        {
                            "name": "*",
                            "reason": "skill_registry_unavailable",
                        },
                    ),
                ),
                (
                    {
                        "kind": "skill",
                        "name": "*",
                        "reason": "skill_registry_unavailable",
                    },
                ),
                [],
            )
        query = (
            request.knowledge_query
            or request.mission
            or f"{request.node_type} {request.target_ref}"
        )
        if (
            self._skill_retriever is not None
            and request.skill_retrieval_level
            not in ("none", "off", "disabled")
        ):
            try:
                result = self._skill_retriever.search(
                    query,
                    node_type=request.node_type,
                    allowed_tools=request.allowed_tools,
                    runner="any",
                    limit=max(1, request.skill_selection_limit),
                    require_trigger=False,
                )
                if result.included:
                    projection = SkillProjection(
                        node_type=request.node_type,
                        included=tuple(
                            candidate.skill
                            for candidate in result.included
                        ),
                        omitted=tuple(result.omitted),
                        scores=tuple(
                            candidate.score
                            for candidate in result.included
                        ),
                        channels=result.channels,
                    )
                    projection = _filter_allowed_skills(
                        projection,
                        request.allowed_skills,
                    )
                    return (
                        projection,
                        tuple(
                            {
                                "kind": "skill",
                                **item,
                            }
                            for item in projection.omitted
                        ),
                        list(result.degraded),
                    )
            except Exception:
                pass
        projection = build_skill_projection(
            node_type=request.node_type,
            skills=self._skills.list(),
            allowed_tools=request.allowed_tools,
            runner="any",
            registry=self._skills,
            selection_limit=request.skill_selection_limit,
            require_trigger=False,
        )
        projection = _filter_allowed_skills(
            projection,
            request.allowed_skills,
        )
        omitted = tuple(
            {
                "kind": "skill",
                **item,
            }
            for item in projection.omitted
        )
        return projection, omitted, []

    def _project_mcp(
        self,
        request: ContextRequest,
    ) -> tuple[
        tuple[ToolPreview, ...],
        tuple[dict[str, str], ...],
    ]:
        if request.mcp_config is None:
            return (), ()
        try:
            connector = self._mcp_factory(request.mcp_config)
            previews = connector.list_tools(timeout=request.mcp_timeout)
        except Exception as error:
            name = str(request.mcp_config.get("name", "*"))
            return (), (
                {
                    "kind": "mcp",
                    "name": name,
                    "reason": (
                        f"mcp_discovery_failed:{type(error).__name__}"
                    ),
                },
            )
        included, omitted = project_tools(
            previews,
            node_type=request.node_type,
            allowed_tools=request.allowed_tools,
        )
        return included, tuple(
            {
                "kind": "mcp",
                **item,
            }
            for item in omitted
        )
