from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def generate_sbom(
    *,
    tool_name: str,
    tool_version: str,
    components: list[dict[str, Any]],
) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": tool_name,
                "version": tool_version,
            }
        },
        "components": components,
    }


def generate_sbom_from_manifest(path: str | Path) -> dict:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    components = []
    for name, version in manifest.get("pythonDependencies", {}).items():
        components.append(
            {
                "type": "library",
                "name": f"pypi:{name}",
                "version": version,
                "licenses": [],
            }
        )
    for name, version in manifest.get("webPlane", {}).items():
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "licenses": [],
            }
        )
    images_meta_path = Path(path).parent / "images.json"
    if images_meta_path.exists():
        images_meta = json.loads(images_meta_path.read_text(encoding="utf-8"))
        for name, meta in images_meta.get("images", {}).items():
            components.append(
                {
                    "type": "container",
                    "name": name,
                    "version": meta.get("digest", ""),
                    "description": (
                        f"{meta.get('os', 'unknown')}/"
                        f"{meta.get('architecture', 'unknown')}"
                    ),
                    "licenses": [],
                }
            )
    else:
        for name, digest in manifest.get("container", {}).get(
            "imageDigests", {}
        ).items():
            components.append(
                {
                    "type": "container",
                    "name": name,
                    "version": digest,
                    "licenses": [],
                }
            )
    return generate_sbom(
        tool_name="veridix",
        tool_version=manifest.get("runtime", {}).get("python", "0.1.0"),
        components=components,
    )
