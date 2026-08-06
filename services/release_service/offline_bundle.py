from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


def create_bundle(
    out_path: str | Path,
    *,
    files: dict[str, bytes],
    metadata: dict | None = None,
) -> dict:
    manifest: dict[str, str] = {}
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
            manifest[name] = hashlib.sha256(data).hexdigest()
        bundle.writestr(
            "manifest.json",
            json.dumps({"metadata": metadata or {}, "files": manifest}),
        )
    return {"files": len(files), "manifest": manifest}


def verify_bundle(path: str | Path) -> tuple[bool, list[str]]:
    failures: list[str] = []
    with zipfile.ZipFile(path, "r") as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        for name, expected in manifest["files"].items():
            actual = hashlib.sha256(bundle.read(name)).hexdigest()
            if actual != expected:
                failures.append(name)
    return (not failures, failures)
