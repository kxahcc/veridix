from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="install an airgap bundle and verify its artifacts"
    )
    parser.add_argument("--airgap", required=True)
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print(
            json.dumps(
                {
                    "action": "verify_release_artifacts",
                    "airgap": args.airgap,
                    "steps": [
                    "install_airgap",
                    "check desktop zip contents",
                    "run veridix.exe --version",
                    "run veridix.exe pack list",
                    ],
                    "dry_run": True,
                },
                indent=2,
            )
        )
        return 0

    from services.release_service.airgap import install_airgap
    from services.release_service.policy import LicensePolicy

    out = Path(args.out or tempfile.mkdtemp(prefix="veridix-verify-"))
    result = install_airgap(
        Path(args.airgap),
        out,
        args.public_key,
        LicensePolicy(allowed=("Apache-2.0", "MIT")),
    )
    desktop_zip = Path(result["desktop_zip"])
    with zipfile.ZipFile(desktop_zip) as bundle:
        names = bundle.namelist()
        exe_entry = next(
            name for name in names if name.endswith("veridix.exe")
        )
        bundle.extractall(out)
    exe_path = out / exe_entry
    version = subprocess.run(
        [str(exe_path), "--version"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    pack_list = subprocess.run(
        [str(exe_path), "pack", "list"],
        capture_output=True,
        text=True,
        check=True,
    )
    packs = [
        json.loads(line)
        for line in pack_list.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    assert len(packs) == 9, f"expected 9 packs, got {len(packs)}"
    assert any(pack["name"] == "web" for pack in packs)
    tool_count = sum(int(pack["tools"]) for pack in packs)
    print(
        json.dumps(
            {
                "action": "verify_release_artifacts",
                "installed": str(out),
                "tools_tar": result["tools_tar"],
                "desktop_zip": str(desktop_zip),
                "exe": str(exe_path),
                "version": version,
                "pack_count": len(packs),
                "tool_count": tool_count,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
