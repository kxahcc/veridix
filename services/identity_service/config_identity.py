from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def product_identity_digest(
    *,
    config: dict[str, Any] | None = None,
    tool_environment: dict[str, Any] | None = None,
    runtime_versions: dict[str, Any] | None = None,
) -> str:
    """Canonical product identity binding config, tool env and runtime."""
    payload = {
        "config": config or {},
        "tool_environment": tool_environment or {},
        "runtime_versions": runtime_versions or {},
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_tool_environment(runtime_dir: str | Path) -> dict[str, Any]:
    path = Path(runtime_dir) / "tool-environment.json"
    if not path.exists():
        return {"available": False, "digest": ""}
    try:
        return load_json(path)
    except Exception:
        return {"available": False, "digest": ""}


def load_runtime_versions(
    path: str | Path | None = None,
) -> dict[str, Any]:
    if path is None:
        path = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "manifests"
            / "versions.json"
        )
    try:
        return load_json(path)
    except Exception:
        return {}
