from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="build web/tui/cli, package desktop, export tools image, "
        "and assemble a signed airgap bundle"
    )
    parser.add_argument("--out", default=str(ROOT / "dist-product" / "release"))
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--key", default="")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--installer",
        action="store_true",
        help="also build the Windows self-extracting setup.exe",
    )
    parser.add_argument(
        "--with-python",
        default="",
        help="bundle a portable Python runtime into the installer",
    )
    args = parser.parse_args()

    out = Path(args.out)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "action": "release_artifacts",
                    "out": str(out),
                    "version": args.version,
                    "steps": [
                        "build web/tui/cli",
                        "package desktop zip",
                        "export veridix-tools.tar.gz",
                        "assemble signed airgap bundle",
                        "build windows setup.exe",
                    ],
                    "dry_run": True,
                },
                indent=2,
            )
        )
        return 0
    if not args.key:
        raise SystemExit("--key is required unless --dry-run")

    if not args.skip_build:
        for app in ("web", "cli", "tui"):
            subprocess.run(
                ["npm.cmd", "run", "build"],
                cwd=str(ROOT / "apps" / app),
                check=True,
            )
    desktop_out = out / "desktop"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "package_desktop.py"),
            "--out",
            str(desktop_out),
            "--version",
            args.version,
            "--skip-build",
            "--zip",
        ],
        cwd=str(ROOT),
        check=True,
    )
    desktop_zip = desktop_out.parent / "veridix-desktop.zip"
    setup_exe = ""
    if args.installer:
        installer_command = [
            sys.executable,
            str(ROOT / "scripts" / "build_windows_installer.py"),
            "--version",
            args.version,
            "--out",
            str(out),
            "--payload-dir",
            str(desktop_out),
        ]
        if args.with_python:
            installer_command += ["--with-python", args.with_python]
        subprocess.run(
            installer_command,
            cwd=str(ROOT),
            check=True,
        )
        setup_exe = str(out / "veridix-setup.exe")
    tools_tar = out / "veridix-tools.tar.gz"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "services.tool_pack.pack_cli",
            "export",
            "--out",
            str(tools_tar),
        ],
        cwd=str(ROOT),
        check=True,
    )
    airgap_out = out / "veridix-airgap.zip"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "services.tool_pack.pack_cli",
            "airgap",
            "--out",
            str(airgap_out),
            "--desktop-zip",
            str(desktop_zip),
            "--tools-tar",
            str(tools_tar),
            "--key",
            args.key,
        ],
        cwd=str(ROOT),
        check=True,
    )
    print(
        json.dumps(
            {
                "action": "release_artifacts",
                "desktop_zip": str(desktop_zip),
                "setup_exe": setup_exe,
                "tools_tar": str(tools_tar),
                "airgap": str(airgap_out),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
