from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yaml


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


TRUST_ORDER = {
    "system": 0,
    "user_approved": 1,
    "project_trusted": 2,
    "retrieved_untrusted": 3,
}


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    source_ref: str
    content: str
    project_id: str = ""
    target_refs: tuple[str, ...] = ()
    trust: str = "project_trusted"
    version: str = "1"
    subjects: tuple[str, ...] = ()
    observed_at: str = field(default_factory=utc_now)
    expires_at: str | None = None
    graph: dict[str, Any] | None = None


@dataclass(frozen=True)
class FactRecord:
    fact_id: str
    subject: str
    predicate: str
    value: str
    target: str = ""
    source_refs: tuple[str, ...] = ()
    confidence: float = 0.5
    trust: str = "project_observed"
    observed_at: str = field(default_factory=utc_now)
    expires_at: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillManifest:
    name: str
    version: str
    trigger: str
    description: str = ""
    content: str = ""
    required_tools: tuple[str, ...] = ()
    required_runner: str = "container"
    risk_level: str = "L1"
    content_trust: str = "project_trusted"
    source: str = "builtin"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    minimal_regression: bool = False
    signature: str = ""
    regression_scenarios: tuple[dict[str, Any], ...] = ()
    category: str = ""
    tags: tuple[str, ...] = ()
    cwe_ids: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    chains_with: tuple[str, ...] = ()
    severity_boost: dict[str, str] = field(default_factory=dict)
    references: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    license: str = ""
    package_path: str = ""
    files: tuple[str, ...] = ()


def parse_skill_manifest(data: dict[str, Any]) -> SkillManifest:
    missing = [key for key in ("name", "version", "trigger") if not data.get(key)]
    if missing:
        raise ValueError(f"skill manifest missing fields: {', '.join(missing)}")
    return SkillManifest(
        name=data["name"],
        version=data["version"],
        trigger=_trigger_string(data["trigger"]),
        description=str(data.get("description", "")),
        content=str(data.get("content", "")),
        required_tools=tuple(data.get("required_tools", ())),
        required_runner=data.get("required_runner", "container"),
        risk_level=data.get("risk_level", "L1"),
        content_trust=data.get("content_trust", "project_trusted"),
        source=data.get("source", "builtin"),
        input_schema=data.get("input_schema", {}),
        output_schema=data.get("output_schema", {}),
        minimal_regression=bool(data.get("minimal_regression", False)),
        signature=str(data.get("signature", "")),
        regression_scenarios=tuple(
            data.get("regression_scenarios", ()) or ()
        ),
        category=str(data.get("category", "")),
        tags=_string_tuple(data.get("tags", ())),
        cwe_ids=_string_tuple(data.get("cwe_ids", ())),
        prerequisites=_string_tuple(data.get("prerequisites", ())),
        chains_with=_string_tuple(data.get("chains_with", ())),
        severity_boost=dict(data.get("severity_boost", {}) or {}),
        references=_string_tuple(data.get("references", ())),
        authors=_string_tuple(data.get("authors", ())),
        license=str(data.get("license", "")),
        package_path=str(data.get("package_path", "")),
        files=_string_tuple(data.get("files", ())),
    )


def parse_skill_markdown(text: str, path_hint: str = "") -> dict[str, Any]:
    """Parse a SKILL.md-style file with YAML frontmatter into a manifest dict."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"skill markdown missing frontmatter: {path_hint}")
    end: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        raise ValueError(
            f"skill markdown frontmatter not closed: {path_hint}"
        )
    front = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).strip()
    data = yaml.safe_load(front) or {}
    if not isinstance(data, dict):
        raise ValueError(f"skill frontmatter must be a mapping: {path_hint}")
    data.setdefault("content", body)
    return _normalize_skill_fields(data)


def _normalize_skill_fields(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    if normalized.get("name") is not None:
        normalized["name"] = str(normalized["name"])
    if normalized.get("version") is not None:
        normalized["version"] = str(normalized["version"])
    for key in (
        "required_tools",
        "tags",
        "cwe_ids",
        "prerequisites",
        "chains_with",
        "references",
        "authors",
    ):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = [
                item.strip() for item in value.split(",") if item.strip()
            ]
    if isinstance(normalized.get("trigger"), list):
        normalized["trigger"] = ",".join(
            map(str, normalized["trigger"])
        )
    for key in ("required_tools", "tags", "cwe_ids", "prerequisites", "chains_with", "references", "authors"):
        value = normalized.get(key)
        if isinstance(value, list):
            normalized[key] = tuple(map(str, value))
    for key in ("input_schema", "output_schema"):
        value = normalized.get(key)
        if value is None:
            normalized[key] = {}
    for key in ("severity_boost",):
        value = normalized.get(key)
        if value is None:
            normalized[key] = {}
    if normalized.get("regression_scenarios") is None:
        normalized["regression_scenarios"] = ()
    return normalized


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(
            item.strip() for item in value.split(",") if item.strip()
        )
    return tuple(str(item) for item in value)


def _trigger_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return ",".join(str(item) for item in value)
