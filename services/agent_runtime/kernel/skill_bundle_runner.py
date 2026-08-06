from __future__ import annotations

import re
from pathlib import Path
import os

from .contracts import ExecutionRequest, ExecutionResult


_SAFE_REF = re.compile(r"^[A-Za-z0-9_.-]+$")
_TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".py",
        ".sh",
        ".ps1",
        ".html",
        ".xml",
        ".csv",
    }
)


class SkillBundleRunner:
    """Read-only access to skill package resources for the agent.

    The model can read `references/` and `scripts/` content from an included
    skill. Paths are resolved against the skill package root and rejected
    when they escape it, point at directories, are too large, or use a
    non-text suffix. This is knowledge access, never execution.
    """

    def __init__(
        self,
        assets_dir: str | Path,
        *,
        max_bytes: int = 65_536,
    ) -> None:
        self._skills_root = Path(assets_dir) / "skills" / "builtin"
        self._max_bytes = max(1024, int(max_bytes))

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.tool_ref != "skill.read":
            raise ValueError(f"unsupported skill tool {request.tool_ref}")
        skill_ref = str(request.input.get("skill_ref") or "").strip()
        rel_path = str(request.input.get("path") or "").strip()
        if not _SAFE_REF.match(skill_ref):
            return self._failed(request, "invalid_skill_ref")
        if not rel_path or rel_path.startswith(("/", "\\")):
            return self._failed(request, "invalid_path")
        roots = [
            self._skills_root,
            Path(os.environ.get("VERIDIX_RUNTIME_DIR", "runtime")) / "skills",
        ]
        for root in roots:
            base = (root / skill_ref).resolve()
            if not base.is_dir() or not _is_within(root, base):
                continue
            target = (base / rel_path).resolve()
            if not _is_within(base, target):
                return self._failed(request, "path_escapes_skill")
            if not target.is_file():
                continue
            if target.suffix.lower() not in _TEXT_SUFFIXES:
                return self._failed(request, "unsupported_file_type")
            try:
                size = target.stat().st_size
            except OSError:
                return self._failed(request, "unreadable_file")
            if size > self._max_bytes:
                return self._failed(request, "file_too_large")
            try:
                content = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return self._failed(request, "unreadable_file")
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                exit_code=0,
                stdout=content,
                stderr="",
                side_effect_state="known",
            )
        return self._failed(request, "file_not_found")

    @staticmethod
    def _failed(request: ExecutionRequest, reason: str) -> ExecutionResult:
        return ExecutionResult(
            action_id=request.action_id,
            status="failed",
            exit_code=2,
            stdout="",
            stderr=f"skill.read failed: {reason}",
            side_effect_state="known",
        )


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False
