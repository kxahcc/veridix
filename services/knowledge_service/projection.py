from __future__ import annotations

from dataclasses import dataclass, field

from .models import KnowledgeChunk, SkillManifest


@dataclass(frozen=True)
class KnowledgeView:
    node_type: str
    chunks: tuple[KnowledgeChunk, ...]
    omitted: tuple[dict[str, str], ...]
    token_estimate: int = 0


@dataclass(frozen=True)
class SkillProjection:
    node_type: str
    included: tuple[SkillManifest, ...]
    omitted: tuple[dict[str, str], ...]
    scores: tuple[float, ...] = ()
    channels: tuple[str, ...] = ()


def build_knowledge_view(
    *,
    node_type: str,
    chunks: list[KnowledgeChunk],
    trust_max: str = "project_trusted",
    token_budget: int = 2000,
) -> KnowledgeView:
    from .models import TRUST_ORDER

    max_rank = TRUST_ORDER.get(trust_max, 3)
    included: list[KnowledgeChunk] = []
    omitted: list[dict[str, str]] = []
    tokens = 0
    for chunk in chunks:
        rank = TRUST_ORDER.get(chunk.trust, 3)
        if rank > max_rank:
            omitted.append(
                {"chunk_id": chunk.chunk_id, "reason": "trust_denied"}
            )
            continue
        estimate = len(chunk.content.split()) * 2
        if tokens + estimate > token_budget:
            omitted.append(
                {"chunk_id": chunk.chunk_id, "reason": "token_budget_exceeded"}
            )
            continue
        included.append(chunk)
        tokens += estimate
    return KnowledgeView(
        node_type=node_type,
        chunks=tuple(included),
        omitted=tuple(omitted),
        token_estimate=tokens,
    )


def build_skill_projection(
    *,
    node_type: str,
    skills: list[SkillManifest],
    allowed_tools: tuple[str, ...],
    runner: str,
    registry,
    selection_limit: int = 6,
    require_trigger: bool = True,
) -> SkillProjection:
    included: list[SkillManifest] = []
    omitted: list[dict[str, str]] = []
    for skill in skills:
        ok, reason = registry.project_for_node(
            skill,
            node_type=node_type,
            allowed_tools=allowed_tools,
            runner=runner,
            require_trigger=require_trigger,
        )
        if ok:
            included.append(skill)
        else:
            omitted.append({"name": skill.name, "version": skill.version, "reason": reason})
    included.sort(
        key=lambda skill: (
            _skill_priority(skill),
            skill.name,
        )
    )
    if selection_limit and len(included) > selection_limit:
        for skill in included[selection_limit:]:
            omitted.append(
                {
                    "name": skill.name,
                    "version": skill.version,
                    "reason": "selection_limit_exceeded",
                }
            )
        included = included[:selection_limit]
    return SkillProjection(
        node_type=node_type,
        included=tuple(included),
        omitted=tuple(omitted),
        scores=(),
        channels=(),
    )


def _skill_priority(skill: SkillManifest) -> int:
    name = skill.name
    if name.startswith(("veridix-", "web-", "verifier", "host.")):
        return 0
    if name.startswith("strix-"):
        return 1
    if name.startswith("cyberstrike"):
        return 2
    return 3
