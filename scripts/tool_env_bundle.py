#!/usr/bin/env python
"""Bundle or load the tool environment image for offline delivery.

Usage:
  python scripts/tool_env_bundle.py save --out dist-product
  python scripts/tool_env_bundle.py load --tar dist-product/veridix-tools-<digest>.tar
  python scripts/tool_env_bundle.py check
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/kxahcc/veridix/veridix-tools:full"


def _run(args: list[str], timeout: float = 1800.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _digest() -> str:
    result = _run(
        ["docker", "inspect", IMAGE, "--format", "{{index .RepoDigests 0}}"]
    )
    if result.returncode == 0:
        return result.stdout.strip().split("@")[-1]
    return ""


def _save(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = _digest()
    short = digest.replace("sha256:", "")[:12] if digest else "unknown"
    tar_path = out_dir / f"veridix-tools-{short}.tar"
    result = _run(["docker", "save", IMAGE, "-o", str(tar_path)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1000:])
    manifest = {
        "image": IMAGE,
        "digest": digest,
        "tar": tar_path.name,
        "size_bytes": tar_path.stat().st_size,
        "created_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    (out_dir / "veridix-tools-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return manifest


def _load(tar_path: Path) -> dict:
    result = _run(["docker", "load", "-i", str(tar_path)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1000:])
    return {
        "loaded_from": str(tar_path),
        "output": result.stdout.strip(),
    }


def _check() -> dict:
    result = _run(
        [
            "docker",
            "image",
            "inspect",
            IMAGE,
            "--format",
            "{{.Id}}",
        ],
        timeout=60.0,
    )
    return {
        "image": IMAGE,
        "present": result.returncode == 0,
        "digest": _digest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    save = sub.add_parser("save")
    save.add_argument("--out", default="dist-product")
    load = sub.add_parser("load")
    load.add_argument("--tar", required=True)
    sub.add_parser("check")
    args = parser.parse_args()
    if args.command == "save":
        print(json.dumps(_save(Path(args.out)), indent=2))
    elif args.command == "load":
        print(json.dumps(_load(Path(args.tar)), indent=2))
    else:
        print(json.dumps(_check(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
