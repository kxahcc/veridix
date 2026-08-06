from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .registry import ToolRegistry


ROOT = Path(
    os.environ.get("VERIDIX_ROOT", Path(__file__).resolve().parents[2])
)
PACK_DIR = ROOT / "deploy" / "toolpacks"
DEFAULT_PACKS = (
    "base",
    "web",
    "network",
    "host",
    "ad",
    "code",
    "cloud",
    "binary",
    "vulnscan",
)
DOWNLOAD_DIR = ROOT / "deploy" / "container" / "veridix-tools" / "downloads"
REQUIRED_BINARIES = {
    "nuclei.zip": "nuclei",
    "fscan": "fscan",
    "metasploit.deb": "metasploit",
}


def ensure_packs(
    names: tuple[str, ...] = DEFAULT_PACKS,
    *,
    dry_run: bool = False,
    build: bool = False,
    fetch: bool = False,
    registry: str = "",
) -> list[dict]:
    if dry_run:
        return [
            {
                "name": name,
                "action": "install",
                "status": "planned",
                "health": "dry_run",
                "build": build,
                "fetch": fetch,
                "registry": registry,
            }
            for name in names
        ]
    registry = ToolRegistry()
    for path in sorted(PACK_DIR.glob("*.json")):
        registry.load_manifest(path)
    results: list[dict] = []
    for name in names:
        record = registry.install(name)
        if record.status == "disabled":
            results.append(
                {
                    "name": name,
                    "action": "install",
                    "status": "disabled",
                    "health": "disabled",
                }
            )
            continue
        if record.health != "ok":
            resolved = _resolve_image(
                registry=registry,
                fetch=fetch,
                build=build,
            )
            if resolved:
                record = registry.install(name)
            else:
                record.health = (
                    "missing_binaries"
                    if _missing_binaries()
                    else "build_failed"
                )
        results.append(
            {
                "name": name,
                "action": "install",
                "status": record.status,
                "health": record.health,
            }
        )
    return results


def _resolve_image(
    *,
    registry: str,
    fetch: bool,
    build: bool,
) -> bool:
    if registry and _pull_tools_image(registry):
        return True
    if fetch and _fetch_binaries():
        return _build_tools_image()
    if build and not _missing_binaries():
        return _build_tools_image()
    return False


def _missing_binaries(
    downloads_dir: Path = DOWNLOAD_DIR,
) -> list[str]:
    return [
        name
        for name in REQUIRED_BINARIES
        if not (downloads_dir / name).exists()
    ]


def _fetch_binaries() -> bool:
    try:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "fetch_tool_binaries.py"),
            ],
            check=True,
            timeout=1800,
        )
        return True
    except Exception:
        return False


def _pull_tools_image(registry: str) -> bool:
    source = _registry_source(registry)
    try:
        subprocess.run(
            ["docker", "pull", source],
            check=True,
            timeout=600,
        )
        subprocess.run(
            ["docker", "tag", source, "veridix-tools:full"],
            check=True,
            timeout=60,
        )
        return True
    except Exception:
        return False


def _registry_source(registry: str) -> str:
    payload = json.loads(
        (ROOT / "deploy" / "manifests" / "images.json").read_text(
            encoding="utf-8"
        )
    )
    digest = str(payload["images"]["veridix-tools-full"]["digest"])
    return f"{registry}/veridix/veridix-tools@{digest}"


def _build_tools_image() -> bool:
    try:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_veridix_tools.py"),
                "--full",
                "--code-lite",
            ],
            check=True,
            timeout=1800,
        )
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="preflight Tool Packs for veridix up"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument(
        "--registry",
        default=os.environ.get("VERIDIX_TOOLS_REGISTRY", ""),
    )
    parser.add_argument("--snapshot-out", default="")
    parser.add_argument("--packs", nargs="*", default=list(DEFAULT_PACKS))
    args = parser.parse_args()
    results = ensure_packs(
        tuple(args.packs),
        dry_run=args.dry_run,
        build=args.build,
        fetch=args.fetch,
        registry=args.registry,
    )
    if args.snapshot_out:
        from .environment import write_environment_snapshot

        write_environment_snapshot(
            args.snapshot_out,
            pack_names=tuple(args.packs),
        )
    print(json.dumps(results, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
