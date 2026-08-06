#!/usr/bin/env python
"""Build Windows release artifacts locally and run release readiness.

Steps: Node SEA exes -> desktop bundle + zip -> manifest -> readiness gate.

Usage:
  python scripts/build_release_local.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str], *, check: bool = True) -> None:
    subprocess.run(args, cwd=str(ROOT), check=check)


def main() -> int:
    _run(["node", "scripts/package_sea.mjs"])
    _run(
        [
            sys.executable,
            "scripts/package_desktop.py",
            "--out",
            "dist-product/desktop",
            "--version",
            "0.1.0",
            "--skip-build",
            "--zip",
        ]
    )
    shutil.copy2(
        ROOT / "dist-product" / "desktop" / "manifest.json",
        ROOT / "dist-product" / "manifest.json",
    )
    env = {
        **os.environ,
        "VERIDIX_RELEASE_OWNER": os.environ.get(
            "VERIDIX_RELEASE_OWNER",
            "local",
        ),
    }
    subprocess.run(
        [
            sys.executable,
            "services/release_service/readiness_cli.py",
            "--run-tests",
            "--out",
            "dist-product/release-readiness.json",
        ],
        cwd=str(ROOT),
        env=env,
        check=True,
    )
    print("release artifacts and readiness gate complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
