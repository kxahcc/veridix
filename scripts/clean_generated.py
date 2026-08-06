#!/usr/bin/env python
"""Remove generated test/release artifacts from the workspace root.

Only known generated paths are removed. Every target is resolved and
verified to stay inside the workspace before deletion.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

GENERATED_DIRS = (
    "runtime",
    "runtime-clean",
    "runtime-clean2",
    "runtime-debug",
    "dist-product",
    ".pytest_tmp",
    ".tmp",
)

GENERATED_PATTERNS = (
    "runtime-clean*",
    "runtime-gate*",
    "runtime-debug*",
    "runtime-protocol*",
)

GENERATED_FILES = (
    "veridix-desktop.zip",
    "*.tar",
)


def _candidates() -> list[Path]:
    found: list[Path] = []
    for name in GENERATED_DIRS:
        path = ROOT / name
        if path.exists():
            found.append(path)
    for pattern in GENERATED_PATTERNS:
        found.extend(ROOT.glob(pattern))
    for pattern in GENERATED_FILES:
        found.extend(ROOT.glob(pattern))
    return sorted(
        {path.resolve() for path in found},
        key=lambda path: str(path),
    )


def _verify(path: Path) -> None:
    root = ROOT.resolve()
    if not str(path).startswith(str(root)):
        raise RuntimeError(f"refusing to remove path outside workspace: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="clean generated runtime/release artifacts"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be removed without deleting",
    )
    args = parser.parse_args()

    candidates = _candidates()
    if not candidates:
        print("nothing to clean")
        return 0
    for path in candidates:
        _verify(path)
    for path in candidates:
        label = "would remove" if args.dry_run else "removing"
        print(f"{label}: {path}")
        if not args.dry_run:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    if args.dry_run:
        print("dry run complete; rerun without --dry-run to delete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
