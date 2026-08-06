from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OfflineComponent:
    ecosystem: str
    name: str
    version: str
    integrity: str | None = None
    source: str | None = None


def parse_npm_lock(lock: dict[str, Any]) -> list[OfflineComponent]:
    components: list[OfflineComponent] = []
    packages = lock.get("packages", {}) if isinstance(lock, dict) else {}
    for path, meta in packages.items():
        if path == "" or not isinstance(meta, dict):
            continue
        name = meta.get("name")
        if not name:
            segments = [
                segment
                for segment in path.split("/")
                if segment and segment != "node_modules"
            ]
            name = "/".join(segments)
        version = meta.get("version")
        if not name or not version:
            continue
        components.append(
            OfflineComponent(
                ecosystem="npm",
                name=str(name),
                version=str(version),
                integrity=meta.get("integrity"),
                source=meta.get("resolved"),
            )
        )
    return sorted(components, key=lambda item: (item.name, item.version))


def parse_python_lock(text: str) -> list[OfflineComponent]:
    components: list[OfflineComponent] = []
    pending: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        hashes = pending.get("hashes", [])
        components.append(
            OfflineComponent(
                ecosystem="python",
                name=pending["name"],
                version=pending["version"],
                integrity=hashes[0] if hashes else None,
            )
        )
        pending = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash="):
            if pending is not None:
                pending.setdefault("hashes", []).append(line[len("--hash=") :])
            continue
        if "==" in line and not line.startswith("-"):
            flush()
            name, version = line.split("==", 1)
            pending = {
                "name": name.strip(),
                "version": version.strip(),
                "hashes": [],
            }
    flush()
    return sorted(components, key=lambda item: (item.name, item.version))


def build_offline_manifest(
    *,
    npm_lock: dict[str, Any] | None = None,
    python_lock: str | None = None,
) -> dict[str, Any]:
    components: list[OfflineComponent] = []
    if npm_lock is not None:
        components.extend(parse_npm_lock(npm_lock))
    if python_lock is not None:
        components.extend(parse_python_lock(python_lock))
    components.sort(key=lambda item: (item.ecosystem, item.name, item.version))
    return {
        "schemaVersion": 1,
        "components": [asdict(component) for component in components],
    }


def offline_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, indent=2, ensure_ascii=True).encode("utf-8")


def verify_offline_cache(
    manifest: dict[str, Any],
    cache_dir: str | Path,
) -> list[str]:
    failures: list[str] = []
    for component in manifest.get("components", []):
        name = str(component.get("name", ""))
        version = str(component.get("version", ""))
        candidates = sorted(Path(cache_dir).glob(f"{name}-{version}*"))
        if not candidates:
            failures.append(f"missing:{name}=={version}")
            continue
        integrity = component.get("integrity")
        if integrity and integrity.startswith("sha256:"):
            actual = hashlib.sha256(candidates[0].read_bytes()).hexdigest()
            if actual != integrity[len("sha256:") :]:
                failures.append(f"hash:{name}=={version}")
    return failures


def assemble_offline_deps(
    manifest: dict[str, Any],
    cache_dir: str | Path,
) -> dict[str, bytes]:
    failures = verify_offline_cache(manifest, cache_dir)
    if failures:
        raise ValueError(f"offline cache incomplete: {', '.join(failures)}")
    files: dict[str, bytes] = {}
    for component in manifest.get("components", []):
        name = str(component["name"])
        version = str(component["version"])
        ecosystem = str(component.get("ecosystem", "unknown"))
        candidates = sorted(Path(cache_dir).glob(f"{name}-{version}*"))
        path = candidates[0]
        files[f"deps/{ecosystem}/{path.name}"] = path.read_bytes()
    return files
