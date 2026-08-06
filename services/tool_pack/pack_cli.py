from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .registry import ToolRegistry


ROOT = Path(
    os.environ.get("VERIDIX_ROOT", Path(__file__).resolve().parents[2])
)
PACK_DIR = ROOT / "deploy" / "toolpacks"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for path in sorted(PACK_DIR.glob("*.json")):
        registry.load_manifest(path)
    return registry


def list_packs() -> list[dict]:
    registry = _registry()
    return [
        {
            "name": name,
            "version": record.manifest.version,
            "tools": len(record.manifest.tools),
            "status": record.status,
        }
        for name, record in sorted(registry._packs.items())
    ]


def show_pack(name: str) -> dict:
    registry = _registry()
    record = registry._require(name)
    definitions = [
        {
            "ref": definition.ref,
            "risk_level": definition.risk_level,
            "runner": definition.runner,
        }
        for definition in registry.list()
        if definition.pack == name
    ]
    return {
        "name": name,
        "version": record.manifest.version,
        "image": record.manifest.image,
        "digest": record.manifest.digest,
        "capabilities": list(record.manifest.capabilities),
        "tools": definitions,
        "status": record.status,
    }


def install_pack(name: str, *, dry_run: bool = False) -> dict:
    registry = _registry()
    if dry_run:
        return {
            "name": name,
            "action": "install",
            "dry_run": True,
            "hint": (
                "run without --dry-run to probe the local image "
                "or set VERIDIX_TOOLS_IMAGE to a reachable registry"
            ),
        }
    record = registry.install(name)
    return {
        "name": name,
        "status": record.status,
        "health": record.health,
    }


def export_image(
    *,
    out: str,
    dry_run: bool = False,
) -> dict:
    target = Path(out)
    if dry_run:
        return {
            "action": "export",
            "image": "veridix-tools:full",
            "out": str(target),
            "dry_run": True,
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".tar",
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            ["docker", "save", "-o", str(tmp_path), "veridix-tools:full"],
            check=True,
            timeout=1800,
        )
        with tmp_path.open("rb") as source, gzip.open(
            target,
            "wb",
        ) as sink:
            shutil.copyfileobj(source, sink)
    finally:
        tmp_path.unlink(missing_ok=True)
    return {
        "action": "export",
        "image": "veridix-tools:full",
        "out": str(target),
        "bytes": target.stat().st_size,
    }


def assemble_airgap(
    *,
    out: str,
    desktop_zip: str,
    tools_tar: str,
    key: str,
    sbom: str = "",
    versions: str = "",
    knowledge_index: str = "",
    dry_run: bool = False,
) -> dict:
    if dry_run:
        return {
            "action": "airgap",
            "out": out,
            "desktop_zip": desktop_zip,
            "tools_tar": tools_tar,
            "dry_run": True,
        }
    from services.release_service.airgap import assemble_airgap_bundle

    desktop_bytes = Path(desktop_zip).read_bytes()
    tools_bytes = Path(tools_tar).read_bytes()
    sbom_payload = (
        json.loads(Path(sbom).read_text(encoding="utf-8"))
        if sbom
        else {"components": []}
    )
    versions_payload = (
        json.loads(Path(versions).read_text(encoding="utf-8"))
        if versions
        else {"runtime": {}}
    )
    knowledge_bytes = (
        Path(knowledge_index).read_bytes()
        if knowledge_index
        else b""
    )
    return assemble_airgap_bundle(
        Path(out),
        images={},
        knowledge_index=knowledge_bytes,
        sbom=sbom_payload,
        versions=versions_payload,
        private_key_hex=key,
        tools_tar=tools_bytes,
        desktop_zip=desktop_bytes,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="manage Tool Packs")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("name")
    install = sub.add_parser("install")
    install.add_argument("name")
    install.add_argument("--dry-run", action="store_true")
    export = sub.add_parser("export")
    export.add_argument("--out", required=True)
    export.add_argument("--dry-run", action="store_true")
    airgap = sub.add_parser("airgap")
    airgap.add_argument("--out", required=True)
    airgap.add_argument("--desktop-zip", required=True)
    airgap.add_argument("--tools-tar", required=True)
    airgap.add_argument("--key", required=True)
    airgap.add_argument("--sbom", default="")
    airgap.add_argument("--versions", default="")
    airgap.add_argument("--knowledge-index", default="")
    airgap.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.command == "list":
        payload = list_packs()
    elif args.command == "show":
        payload = show_pack(args.name)
    elif args.command == "export":
        payload = export_image(out=args.out, dry_run=args.dry_run)
    elif args.command == "airgap":
        payload = assemble_airgap(
            out=args.out,
            desktop_zip=args.desktop_zip,
            tools_tar=args.tools_tar,
            key=args.key,
            sbom=args.sbom,
            versions=args.versions,
            knowledge_index=args.knowledge_index,
            dry_run=args.dry_run,
        )
    else:
        payload = install_pack(args.name, dry_run=args.dry_run)
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
