from __future__ import annotations

import re
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from .models import parse_skill_markdown, parse_skill_manifest
from .skill_conformance import run_skill_conformance


MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def import_skill_package(
    content: bytes,
    *,
    target_root: str | Path,
    filename: str = "skill.zip",
    skill_md: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import a SKILL.md package from a zip or raw SKILL.md text.

    The package is written to ``target_root/<skill_name>/``. Only a
    canonical ``SKILL.md`` manifest is required; supporting files are
    preserved for ``skill.read``. Path traversal and oversized payloads
    are rejected before any file is written.
    """

    root = Path(target_root)
    root.mkdir(parents=True, exist_ok=True)
    if skill_md.strip():
        manifest_data = parse_skill_markdown(
            skill_md,
            path_hint=filename,
        )
        package_entries = [("SKILL.md", skill_md.encode("utf-8"))]
    else:
        package_entries = _extract_zip(content, filename=filename)
        skill_path = next(
            (
                name
                for name, _data in package_entries
                if Path(name).name == "SKILL.md"
            ),
            None,
        )
        if skill_path is None:
            raise ValueError("zip archive does not contain a SKILL.md")
        raw = next(data for name, data in package_entries if name == skill_path)
        manifest_data = parse_skill_markdown(
            raw.decode("utf-8", errors="replace"),
            path_hint=skill_path,
        )
        prefix = str(Path(skill_path).parent)
        normalized: list[tuple[str, bytes]] = []
        for name, data in package_entries:
            if name == skill_path:
                normalized.append(("SKILL.md", data))
            elif prefix == ".":
                normalized.append((name, data))
            elif name.startswith(f"{prefix}/"):
                normalized.append((name[len(prefix) + 1 :], data))
        package_entries = normalized
    manifest = parse_skill_manifest(manifest_data)
    name = str(manifest.name)
    if not _SAFE_NAME.match(name):
        raise ValueError(
            f"skill name {name!r} is not safe; use letters, digits, '.', '_', '-'"
        )
    package_dir = root / name
    if package_dir.exists() and not overwrite:
        raise ValueError(
            f"skill {name} already exists; enable overwrite to replace it"
        )
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for rel_name, data in package_entries:
        rel = Path(rel_name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"invalid package path {rel_name}")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"package file too large: {rel_name}")
        target = (package_dir / rel).resolve()
        if not target.is_relative_to(package_dir.resolve()):
            raise ValueError(f"package path escapes skill directory: {rel_name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(rel.as_posix())

    conformance = run_skill_conformance(
        manifest,
        known_tools=(),
    )
    return {
        "skill_ref": name,
        "name": name,
        "version": manifest.version,
        "description": manifest.description,
        "package_path": f"runtime/skills/{name}",
        "files": sorted(written),
        "conformance": conformance.to_dict(),
        "warnings": list(conformance.issues),
    }


def _extract_zip(content: bytes, *, filename: str) -> list[tuple[str, bytes]]:
    if not filename.lower().endswith(".zip"):
        raise ValueError("skill package must be a .zip archive")
    entries: list[tuple[str, bytes]] = []
    total = 0
    with zipfile.ZipFile(BytesIO(content)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"unsafe path in archive: {info.filename}")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ValueError("skill package is too large")
            entries.append((name, archive.read(info)))
    if not entries:
        raise ValueError("skill package is empty")
    return entries
