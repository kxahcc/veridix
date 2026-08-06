from __future__ import annotations

from pathlib import Path

from services.knowledge_service.loader import load_skills_dir
from services.knowledge_service.models import parse_skill_markdown
from services.knowledge_service.skills import SkillRegistry


SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills" / "builtin"


def test_builtin_skills_use_package_layout() -> None:
    directories = [child for child in SKILLS_DIR.iterdir() if child.is_dir()]
    assert len(directories) >= 62
    for directory in directories:
        skill_file = directory / "SKILL.md"
        assert skill_file.exists(), f"missing {skill_file}"
        data = parse_skill_markdown(
            skill_file.read_text(encoding="utf-8"),
            path_hint=str(skill_file),
        )
        assert data["name"] == directory.name
        assert data["description"]
        assert data["version"]
        assert data["trigger"]


def test_load_skills_dir_discovers_packages() -> None:
    registry = SkillRegistry()
    load_skills_dir(registry, SKILLS_DIR)

    names = {manifest.name for manifest in registry.list()}
    assert "web-discovery" in names
    assert "strix-sqlmap" in names
    assert "web-file-upload" in names
    assert "web-lfi" in names
    assert "web-owasp" in names
    assert "web-dom-xss" in names
    assert len(names) >= 62


def test_core_packages_carry_bundle_resources() -> None:
    registry = SkillRegistry()
    load_skills_dir(registry, SKILLS_DIR)

    by_name = {manifest.name: manifest for manifest in registry.list()}
    orchestration = by_name["veridix-redteam-orchestration"]
    verifier = by_name["verifier"]

    assert orchestration.package_path == "skills/builtin/veridix-redteam-orchestration"
    assert any(
        file.endswith("references/evidence-gate.md")
        for file in orchestration.files
    )
    assert any(
        file.endswith("references/false-positive-checklist.md")
        for file in verifier.files
    )


def test_rich_skill_metadata_is_preserved() -> None:
    registry = SkillRegistry()
    load_skills_dir(registry, SKILLS_DIR)

    cors = registry.get("cyberstrike-cors")
    assert cors is not None
    assert cors.description
    assert "CORS" in cors.description
    assert cors.cwe_ids
    assert cors.tags
    assert len(cors.content) > 1000
