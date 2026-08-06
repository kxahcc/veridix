from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from .models import SkillManifest


KNOWN_RUNNERS = frozenset({"container", "browser", "native"})
KNOWN_RISK_LEVELS = frozenset({"L1", "L2", "L3", "L4"})
KNOWN_TRUST = frozenset({"system", "user_approved", "project_trusted", "retrieved_untrusted"})

INJECTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"(?i)ignore (all |the )?(previous|prior) (instructions|rules|prompt)"
        ),
    ),
    (
        "exfiltrate",
        re.compile(
            r"(?i)(send|upload|exfiltrate).{0,30}(secret|credential|token|key|password)"
        ),
    ),
    (
        "elevate_permission",
        re.compile(
            r"(?i)(elevate|grant|allow).{0,20}(permission|privilege|admin|root|sudo)"
        ),
    ),
)


@dataclass(frozen=True)
class SkillConformanceReport:
    name: str
    ok: bool
    issues: tuple[str, ...] = ()
    prompt_injection: bool = False
    input_schema_valid: bool = True
    required_tools_known: bool = True
    runner_known: bool = True
    risk_valid: bool = True
    minimal_regression: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "issues": list(self.issues),
            "prompt_injection": self.prompt_injection,
            "input_schema_valid": self.input_schema_valid,
            "required_tools_known": self.required_tools_known,
            "runner_known": self.runner_known,
            "risk_valid": self.risk_valid,
            "minimal_regression": self.minimal_regression,
        }


def run_skill_conformance(
    manifest: SkillManifest,
    *,
    known_tools: Iterable[str],
    known_runners: Iterable[str] = KNOWN_RUNNERS,
    risk_levels: Iterable[str] = KNOWN_RISK_LEVELS,
    trust_levels: Iterable[str] = KNOWN_TRUST,
) -> SkillConformanceReport:
    tools = set(known_tools)
    runners = set(known_runners)
    risks = set(risk_levels)
    trusts = set(trust_levels)
    issues: list[str] = []

    if not manifest.name or not manifest.version or not manifest.trigger:
        issues.append("missing_manifest_fields")
    if not manifest.description or len(manifest.description.strip()) < 20:
        issues.append("description_too_short")
    if len(manifest.content.strip()) < 200:
        issues.append("content_too_short")
    if not manifest.input_schema:
        issues.append("input_schema_missing")
    if not manifest.output_schema:
        issues.append("output_schema_missing")
    missing_tools = [
        tool for tool in manifest.required_tools if tool not in tools
    ]
    if missing_tools:
        issues.append(f"required_tools_missing:{','.join(missing_tools)}")
    if manifest.required_runner not in runners:
        issues.append("runner_unknown")
    if manifest.risk_level not in risks:
        issues.append("risk_level_invalid")
    if manifest.content_trust not in trusts:
        issues.append("content_trust_invalid")
    schema_valid = (
        isinstance(manifest.input_schema, dict)
        and isinstance(manifest.output_schema, dict)
    )
    if not schema_valid:
        issues.append("schema_invalid")
    if manifest.minimal_regression and not manifest.regression_scenarios:
        issues.append("minimal_regression_without_scenarios")
    injection = _scan_injection(manifest)
    if injection:
        issues.append(f"prompt_injection:{injection}")
    return SkillConformanceReport(
        name=manifest.name,
        ok=not issues,
        issues=tuple(issues),
        prompt_injection=bool(injection),
        input_schema_valid=schema_valid,
        required_tools_known=not missing_tools,
        runner_known=manifest.required_runner in runners,
        risk_valid=manifest.risk_level in risks,
        minimal_regression=manifest.minimal_regression,
    )


def run_skill_conformance_batch(
    manifests: Iterable[SkillManifest],
    *,
    known_tools: Iterable[str],
) -> list[SkillConformanceReport]:
    return [
        run_skill_conformance(
            manifest,
            known_tools=known_tools,
        )
        for manifest in manifests
    ]


def _scan_injection(manifest: SkillManifest) -> str:
    haystack = json.dumps(
        {
            "name": manifest.name,
            "trigger": manifest.trigger,
            "input_schema": manifest.input_schema,
            "output_schema": manifest.output_schema,
        },
        ensure_ascii=True,
        default=str,
    )
    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(haystack):
            return name
    return ""
