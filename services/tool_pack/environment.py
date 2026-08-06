from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .preflight import DEFAULT_PACKS, PACK_DIR
from .registry import ToolRegistry


def capture_environment(
    pack_names: tuple[str, ...] = DEFAULT_PACKS,
    *,
    builder_version: str = "tool-env-1",
) -> dict:
    registry = ToolRegistry()
    for path in sorted(PACK_DIR.glob("*.json")):
        registry.load_manifest(path)
    packs = []
    for name in pack_names:
        record = registry._require(name)
        manifest = record.manifest
        packs.append(
            {
                "name": name,
                "version": manifest.version,
                "image": manifest.image,
                "digest": manifest.digest,
                "capabilities": list(manifest.capabilities),
                "tools": list(manifest.tools),
                "network": manifest.network,
            }
        )
    payload = {
        "builder_version": builder_version,
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "packs": packs,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
    ).encode("utf-8")
    payload["digest"] = hashlib.sha256(canonical).hexdigest()
    return payload


def write_environment_snapshot(
    path: str | Path,
    *,
    pack_names: tuple[str, ...] = DEFAULT_PACKS,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = capture_environment(pack_names)
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return target
