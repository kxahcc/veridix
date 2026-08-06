from __future__ import annotations

import hashlib
import hmac
import json

from .models import SkillManifest, parse_skill_manifest
from .skill_conformance import run_skill_conformance_batch


def canonical_manifest(manifest: SkillManifest) -> dict:
    return {
        "name": manifest.name,
        "version": manifest.version,
        "trigger": manifest.trigger,
        "description": manifest.description,
        "content": manifest.content,
        "required_tools": sorted(manifest.required_tools),
        "required_runner": manifest.required_runner,
        "risk_level": manifest.risk_level,
        "content_trust": manifest.content_trust,
        "source": manifest.source,
        "input_schema": manifest.input_schema,
        "output_schema": manifest.output_schema,
        "minimal_regression": manifest.minimal_regression,
        "regression_scenarios": manifest.regression_scenarios,
        "category": manifest.category,
        "tags": sorted(manifest.tags),
        "cwe_ids": sorted(manifest.cwe_ids),
        "prerequisites": sorted(manifest.prerequisites),
        "chains_with": sorted(manifest.chains_with),
        "severity_boost": dict(sorted(manifest.severity_boost.items())),
        "references": sorted(manifest.references),
        "authors": sorted(manifest.authors),
        "license": manifest.license,
        "package_path": manifest.package_path,
        "files": sorted(manifest.files),
    }


def sign_manifest(manifest: SkillManifest, key: str) -> SkillManifest:
    payload = _canonical_bytes(manifest)
    signature = hmac.new(
        key.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return SkillManifest(
        **{
            **manifest.__dict__,
            "signature": signature,
        }
    )


def verify_manifest(manifest: SkillManifest, key: str) -> bool:
    if not manifest.signature:
        return False
    expected = hmac.new(
        key.encode("utf-8"),
        _canonical_bytes(manifest),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(manifest.signature, expected)


def _canonical_bytes(manifest: SkillManifest) -> bytes:
    return json.dumps(
        canonical_manifest(manifest),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillManifest] = {}

    def register(
        self,
        manifest_data: dict,
        *,
        verify_key: str | None = None,
    ) -> SkillManifest:
        manifest = parse_skill_manifest(manifest_data)
        if verify_key is not None and not verify_manifest(
            manifest,
            verify_key,
        ):
            raise ValueError(
                f"skill {manifest.name} failed signature verification"
            )
        self._skills[f"{manifest.name}@{manifest.version}"] = manifest
        return manifest

    def get(self, name: str, version: str | None = None) -> SkillManifest | None:
        if version:
            return self._skills.get(f"{name}@{version}")
        candidates = [
            manifest
            for key, manifest in self._skills.items()
            if key.startswith(f"{name}@")
        ]
        return sorted(candidates, key=lambda m: m.version, reverse=True)[0] if candidates else None

    def list(self) -> list[SkillManifest]:
        return sorted(self._skills.values(), key=lambda m: m.name)

    def project_for_node(
        self,
        manifest: SkillManifest,
        *,
        node_type: str,
        allowed_tools: tuple[str, ...],
        runner: str,
        require_trigger: bool = True,
    ) -> tuple[bool, str]:
        if require_trigger and node_type not in manifest.trigger:
            return False, "profile_not_matched"
        missing_tools = [
            tool for tool in manifest.required_tools if tool not in allowed_tools
        ]
        if missing_tools:
            return False, f"required_tools_missing:{','.join(missing_tools)}"
        if runner != "any" and manifest.required_runner != runner:
            return False, "runner_mismatch"
        return True, ""

    def load_builtin(
        self,
        manifests: list[dict],
        *,
        verify_key: str | None = None,
    ) -> int:
        for manifest in manifests:
            self.register(manifest, verify_key=verify_key)
        return len(self._skills)

    def conformance(
        self,
        *,
        known_tools: tuple[str, ...],
    ):
        return run_skill_conformance_batch(
            self.list(),
            known_tools=known_tools,
        )
