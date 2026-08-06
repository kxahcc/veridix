from __future__ import annotations

from dataclasses import dataclass

from services.agent_runtime.context_projector import ContextProjection
from services.agent_runtime.kernel.context import (
    ContentPiece,
    ContentTrustEngine,
    DataLabel,
    DataReleaseDecider,
    ProviderProfile,
    SourceKind,
    TrustLevel,
)
from services.agent_runtime.kernel.contracts import ContextBlocks
from services.agent_runtime.kernel.context_trimming import estimate_tokens
from services.knowledge_service.models import SkillManifest


@dataclass(frozen=True)
class ContextAssemblyResult:
    blocks: ContextBlocks
    omitted: tuple[dict[str, str], ...] = ()
    redacted: int = 0


class ContextAssembler:
    """Turns a node projection into sanitized model context blocks.

    Every block passes through ContentTrust and DataRelease so external
    knowledge, MCP previews and memory cannot change scope, policy or
    secrets before reaching the provider.
    """

    def __init__(
        self,
        *,
        content_trust: ContentTrustEngine | None = None,
        data_release: DataReleaseDecider | None = None,
        skill_token_budget: int = 12000,
    ) -> None:
        self._content_trust = content_trust or ContentTrustEngine()
        self._data_release = data_release or DataReleaseDecider()
        self._skill_token_budget = max(1, int(skill_token_budget))

    def assemble(
        self,
        projection: ContextProjection,
        provider: ProviderProfile,
    ) -> ContextAssemblyResult:
        knowledge, knowledge_omitted, knowledge_redacted = self._knowledge_blocks(
            projection,
            provider,
        )
        memory, memory_omitted, memory_redacted = self._memory_blocks(
            projection,
            provider,
        )
        skills, skill_omitted, skill_redacted = self._skill_blocks(
            projection,
            provider,
        )
        mcp, mcp_omitted, mcp_redacted = self._mcp_blocks(
            projection,
            provider,
        )
        blocks = ContextBlocks(
            knowledge=tuple(knowledge),
            memory=tuple(memory),
            skills=tuple(skills),
            mcp=tuple(mcp),
            summaries=tuple(
                str(item.get("summary") or "")
                for item in projection.memory_summaries
            ),
            digest=projection.context_digest,
        )
        return ContextAssemblyResult(
            blocks=blocks,
            omitted=tuple(
                (*knowledge_omitted, *memory_omitted, *skill_omitted, *mcp_omitted)
            ),
            redacted=(
                knowledge_redacted
                + memory_redacted
                + skill_redacted
                + mcp_redacted
            ),
        )

    def _knowledge_blocks(
        self,
        projection: ContextProjection,
        provider: ProviderProfile,
    ) -> tuple[list[str], list[dict[str, str]], int]:
        lines: list[str] = []
        omitted: list[dict[str, str]] = []
        redacted = 0
        for chunk in projection.knowledge.chunks:
            line = (
                f"[{chunk.chunk_id}] ({chunk.source_ref}) "
                f"trust={chunk.trust}: {chunk.content}"
            )
            released, note, was_redacted = self._release(
                piece_id=chunk.chunk_id,
                content=line,
                source_kind=SourceKind.KNOWLEDGE,
                data_label=DataLabel.PROJECT,
                declared_use="knowledge_context",
                provider=provider,
            )
            if released is None:
                omitted.append(note)
                continue
            redacted += int(was_redacted)
            lines.append(released)
        return lines, omitted, redacted

    def _memory_blocks(
        self,
        projection: ContextProjection,
        provider: ProviderProfile,
    ) -> tuple[list[str], list[dict[str, str]], int]:
        lines: list[str] = []
        omitted: list[dict[str, str]] = []
        redacted = 0
        for view in projection.memory_views:
            if view.status not in ("active", "conflict"):
                continue
            fact = view.fact
            expires = fact.expires_at or "never"
            line = (
                f"fact {fact.fact_id}: {fact.subject} {fact.predicate} = "
                f"{fact.value} [{view.status}, trust={fact.trust}, "
                f"expires={expires}]"
            )
            released, note, was_redacted = self._release(
                piece_id=fact.fact_id,
                content=line,
                source_kind=SourceKind.PROJECT_DOC,
                data_label=DataLabel.PROJECT,
                declared_use="memory_context",
                provider=provider,
            )
            if released is None:
                omitted.append(note)
                continue
            redacted += int(was_redacted)
            lines.append(released)
        return lines, omitted, redacted

    def _skill_blocks(
        self,
        projection: ContextProjection,
        provider: ProviderProfile,
    ) -> tuple[list[str], list[dict[str, str]], int]:
        lines: list[str] = []
        omitted: list[dict[str, str]] = []
        redacted = 0
        used = 0
        for skill in projection.skills.included:
            line = _render_skill(skill)
            estimate = estimate_tokens(line)
            if used + estimate > self._skill_token_budget:
                omitted.append(
                    {
                        "kind": "skill",
                        "name": skill.name,
                        "reason": "skill_token_budget_exceeded",
                    }
                )
                continue
            released, note, was_redacted = self._release(
                piece_id=f"skill:{skill.name}@{skill.version}",
                content=line,
                source_kind=SourceKind.PROJECT_DOC,
                data_label=DataLabel.PUBLIC,
                declared_use="skill_context",
                provider=provider,
            )
            if released is None:
                omitted.append(note)
                continue
            redacted += int(was_redacted)
            lines.append(released)
            used += estimate
        return lines, omitted, redacted

    def _mcp_blocks(
        self,
        projection: ContextProjection,
        provider: ProviderProfile,
    ) -> tuple[list[str], list[dict[str, str]], int]:
        lines: list[str] = []
        omitted: list[dict[str, str]] = []
        redacted = 0
        for tool in projection.mcp_included:
            line = (
                f"{tool.name} ({tool.source}, {tool.trust}): "
                f"{tool.description or 'no description'}"
            )
            released, note, was_redacted = self._release(
                piece_id=tool.name,
                content=line,
                source_kind=SourceKind.MCP_OUTPUT,
                data_label=DataLabel.PUBLIC,
                provider=provider,
            )
            if released is None:
                omitted.append(note)
                continue
            redacted += int(was_redacted)
            lines.append(released)
        return lines, omitted, redacted

    def _release(
        self,
        *,
        piece_id: str,
        content: str,
        source_kind: SourceKind,
        data_label: DataLabel,
        provider: ProviderProfile,
        declared_use: str = "",
    ) -> tuple[str | None, dict[str, str], bool]:
        piece = ContentPiece(
            piece_id=piece_id,
            source_kind=source_kind,
            content=content,
            data_label=data_label,
            declared_use=declared_use,
        )
        trusted = self._content_trust.classify(piece)
        if trusted.trust_level == TrustLevel.ADVERSARIAL:
            return (
                None,
                {
                    "kind": source_kind.value,
                    "name": piece_id,
                    "reason": "adversarial_content_isolated",
                },
                False,
            )
        decision = self._data_release.decide(trusted, provider)
        if decision.decision == "deny":
            return (
                None,
                {
                    "kind": source_kind.value,
                    "name": piece_id,
                    "reason": decision.reason,
                },
                False,
            )
        return (
            decision.content,
            {
                "kind": source_kind.value,
                "name": piece_id,
                "reason": decision.reason,
            },
            decision.decision == "redact",
        )


def _render_skill(skill: SkillManifest) -> str:
    metadata: list[str] = [
        f"Name: {skill.name}@{skill.version}",
        f"Category: {skill.category or 'security-testing'}",
        f"Trigger: {skill.trigger}",
        f"Required tools: {', '.join(skill.required_tools) or '-'}",
        f"Runner: {skill.required_runner}",
        f"Risk: {skill.risk_level}",
    ]
    if skill.description:
        metadata.append(f"Description: {skill.description}")
    if skill.cwe_ids:
        metadata.append(f"CWE: {', '.join(skill.cwe_ids)}")
    if skill.prerequisites:
        metadata.append(f"Prerequisites: {', '.join(skill.prerequisites)}")
    if skill.chains_with:
        metadata.append(f"Chains with: {', '.join(skill.chains_with)}")
    if skill.files:
        metadata.append(f"Bundle files: {', '.join(skill.files[:12])}")
        metadata.append(
            "Use skill.read with this skill_ref and a package-relative "
            "path to read a bundle file."
        )
    return (
        f"### Skill {skill.name}@{skill.version}\n"
        + "\n".join(f"- {line}" for line in metadata)
        + "\n\nInstructions:\n"
        + (skill.content.strip() or "(no instructions)")
    )
