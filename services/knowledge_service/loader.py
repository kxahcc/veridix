from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .knowledge_store import KnowledgeStore
from .models import KnowledgeChunk, parse_skill_markdown
from .skills import SkillRegistry


def load_knowledge_dir(
    store: KnowledgeStore,
    directory: str | Path,
    graph_store=None,
) -> int:
    """Load JSON knowledge packs; each file is a list or {"chunks": [...]}."""
    count = 0
    for path in sorted(Path(directory).glob("*.json")):
        if path.name.startswith("embeddings."):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = _as_entries(payload, key="chunks")
        for entry in entries:
            kwargs: dict[str, Any] = {
                "chunk_id": str(entry["chunk_id"]),
                "source_ref": str(entry["source_ref"]),
                "content": str(entry["content"]),
                "trust": str(entry.get("trust", "project_trusted")),
                "version": str(entry.get("version", "1")),
                "subjects": tuple(entry.get("subjects", ())),
                "target_refs": tuple(entry.get("target_refs", ())),
            }
            if entry.get("observed_at"):
                kwargs["observed_at"] = str(entry["observed_at"])
            if entry.get("expires_at"):
                kwargs["expires_at"] = str(entry["expires_at"])
            if entry.get("graph"):
                kwargs["graph"] = entry["graph"]
            chunk = KnowledgeChunk(**kwargs)
            store.add_chunk(chunk)
            if graph_store is not None:
                graph_store.register_chunk_graph(chunk)
            count += 1
    return count


def load_skills_dir(
    registry: SkillRegistry,
    directory: str | Path,
    *,
    verify_key: str | None = None,
) -> int:
    """Load skill packages from directories containing SKILL.md.

    Canonical layout is ``<directory>/<name>/SKILL.md``; flat Markdown and
    JSON files are accepted for backwards compatibility.
    """
    count = 0
    root = Path(directory)
    for path in _skill_paths(root):
        if path.suffix == ".md":
            manifest = parse_skill_markdown(
                path.read_text(encoding="utf-8"),
                path_hint=str(path),
            )
            if path.parent != root:
                manifest["package_path"] = f"skills/builtin/{path.parent.relative_to(root)}".replace(
                    "\\", "/"
                )
                manifest["files"] = _package_files(path.parent)
            entries = [manifest]
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            entries = _as_entries(payload, key="skills")
        registry.load_builtin(entries, verify_key=verify_key)
        count += len(entries)
    return count


def _skill_paths(directory: Path) -> list[Path]:
    paths: list[Path] = []
    for child in sorted(directory.iterdir()):
        if child.is_dir():
            skill_file = child / "SKILL.md"
            if skill_file.exists():
                paths.append(skill_file)
    paths.extend(sorted(directory.glob("*.md")))
    paths.extend(sorted(directory.glob("*.json")))
    return paths


def _package_files(package_dir: Path) -> tuple[str, ...]:
    """List package-relative bundle files (scripts, assets, references, agents)."""
    if not package_dir.is_dir():
        return ()
    files: list[str] = []
    for child in sorted(package_dir.rglob("*")):
        if not child.is_file():
            continue
        rel = child.relative_to(package_dir).as_posix()
        if rel == "SKILL.md":
            continue
        files.append(rel)
    return tuple(files)


def _as_entries(payload: Any, *, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and key in payload:
        return payload[key]
    if isinstance(payload, dict):
        return [payload]
    return []
