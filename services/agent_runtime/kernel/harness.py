from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .contracts import HarnessSnapshot, NodeSpec, ProjectionSnapshot
from .memory_tools import MEMORY_TOOL_REFS


NATIVE_TOOL_REFS = frozenset(
    {
        "run.finish",
        "skill.read",
        *MEMORY_TOOL_REFS,
    }
)


@dataclass(frozen=True)
class ProviderCapability:
    model_names: tuple[str, ...]
    health: str
    tool_calling: bool = True
    streaming: bool = True
    data_policy: str = "local"


@dataclass(frozen=True)
class ToolEntry:
    name: str
    risk_level: str = "L1"
    required_capability: str = "tool_calling"


@dataclass(frozen=True)
class SkillEntry:
    trigger: str | tuple[str, ...]
    version: str
    min_version: str = "1.0"
    conformance: str = "ok"


@dataclass(frozen=True)
class KnowledgeEntry:
    ref: str
    subjects: tuple[str, ...]
    trust: str = "project_observed"


PROJECT_KNOWLEDGE_TRUST = frozenset(
    {"project_observed", "project_trusted", "user_approved"}
)


def digest(parts: list) -> str:
    canonical = json.dumps(parts, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class HarnessBuilder:
    def __init__(
        self,
        *,
        tools: dict[str, ToolEntry] | None = None,
        skills: dict[str, SkillEntry] | None = None,
        knowledge: dict[str, KnowledgeEntry] | None = None,
        builder_version: str = "wp04-1",
    ) -> None:
        self._tools = tools or {}
        self._skills = skills or {}
        self._knowledge = knowledge or {}
        self._builder_version = builder_version

    def build(
        self,
        node: NodeSpec,
        provider: ProviderCapability,
        *,
        target_ref: str,
        auth_context_ref: str,
        scope_hash: str,
        known_subjects: frozenset[str] = frozenset(),
        graph_version: str = "v1",
        memory_digest: str | None = None,
    ) -> tuple[HarnessSnapshot, ProjectionSnapshot]:
        included_tools: list[str] = []
        omitted: list[dict[str, str]] = []
        allowed_tools = node.allowed_tools or (
            node.loop_spec.allowed_tools if node.loop_spec else ()
        )
        allowed_skills = (
            node.loop_spec.allowed_skills
            if node.loop_spec and node.loop_spec.allowed_skills
            else None
        )
        loop_sandbox = (
            node.loop_spec.sandbox_profile if node.loop_spec else ""
        )
        loop_oracle = node.loop_spec.oracle if node.loop_spec else ""
        loop_success = (
            node.loop_spec.success_criteria if node.loop_spec else ""
        )
        for name in allowed_tools:
            entry = self._tools.get(name)
            if entry is None:
                if name in NATIVE_TOOL_REFS:
                    included_tools.append(name)
                    continue
                omitted.append({"kind": "tool", "name": name, "reason": "tool_not_in_registry"})
                continue
            if not getattr(provider, entry.required_capability, False):
                omitted.append(
                    {
                        "kind": "tool",
                        "name": name,
                        "reason": f"provider_lacks_capability:{entry.required_capability}",
                    }
                )
                continue
            included_tools.append(name)

        included_skills: list[str] = []
        for name, entry in self._skills.items():
            if allowed_skills is not None and name not in allowed_skills:
                omitted.append(
                    {
                        "kind": "skill",
                        "name": name,
                        "reason": "skill_not_in_loop_scope",
                    }
                )
                continue
            if node.harness_profile not in entry.trigger:
                omitted.append({"kind": "skill", "name": name, "reason": "profile_not_matched"})
                continue
            if entry.version < entry.min_version or entry.conformance != "ok":
                omitted.append(
                    {
                        "kind": "skill",
                        "name": name,
                        "reason": (
                            "skill_version_mismatch"
                            if entry.version < entry.min_version
                            else "skill_conformance_failed"
                        ),
                    }
                )
                continue
            included_skills.append(name)

        included_knowledge: list[str] = []
        for ref, entry in self._knowledge.items():
            if node.knowledge_view == "mission" and not (
                set(entry.subjects) & known_subjects
            ):
                omitted.append({"kind": "knowledge", "name": ref, "reason": "knowledge_out_of_scope"})
                continue
            if entry.trust not in PROJECT_KNOWLEDGE_TRUST:
                omitted.append({"kind": "knowledge", "name": ref, "reason": "trust_denied"})
                continue
            included_knowledge.append(ref)

        skill_omitted = [
            item for item in omitted if item.get("kind") == "skill"
        ]
        knowledge_omitted = [
            item for item in omitted if item.get("kind") == "knowledge"
        ]
        projection = ProjectionSnapshot(
            node_id=node.node_id,
            included_tools=tuple(included_tools),
            included_skills=tuple(included_skills),
            knowledge_refs=tuple(included_knowledge),
            omitted=tuple(omitted),
            provider_capability=provider.health,
            trust_notes=("mcp.retrieved_untrusted",),
            memory_digest=(
                memory_digest
                if memory_digest is not None
                else digest(sorted(known_subjects))
            ),
        )
        harness = HarnessSnapshot(
            harness_id=f"harness_{node.node_id}_{graph_version}",
            node_id=node.node_id,
            graph_version=graph_version,
            target_ref=target_ref,
            scope_hash=scope_hash,
            auth_context_ref=auth_context_ref,
            tool_projection_digest=digest(projection.included_tools + tuple(omitted)),
            skill_projection_digest=digest(
                projection.included_skills + tuple(skill_omitted)
            ),
            knowledge_view_digest=digest(
                projection.knowledge_refs + tuple(knowledge_omitted)
            ),
            memory_view_digest=projection.memory_digest,
            sandbox_profile=node.sandbox_profile or loop_sandbox or "S2",
            network_profile="isolated_proxy",
            oracle_policy=node.oracle_ref or loop_oracle or "domain_oracle_required",
            stop_policy=loop_success or "coverage_or_budget",
            budget_policy=json.dumps(
                {
                    **(node.loop_spec.budget if node.loop_spec else {}),
                    "risk_level": (
                        node.loop_spec.risk_level if node.loop_spec else ""
                    ),
                    "evidence_requirements": list(
                        node.loop_spec.evidence_requirements
                        if node.loop_spec
                        else ()
                    ),
                },
                sort_keys=True,
            ),
            provider_capability=provider.health,
            builder_version=self._builder_version,
        )
        return harness, projection
