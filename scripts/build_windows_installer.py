from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _find_7z() -> Path:
    for candidate in (
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ):
        if candidate.exists():
            return candidate
    raise RuntimeError("7-Zip is required to build the SFX installer")


def _package_payload(out: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "package_desktop.py"),
            "--out",
            str(out),
            "--skip-build",
        ],
        cwd=str(ROOT),
        check=True,
        timeout=600,
    )


def _write_install_scripts(stage: Path, version: str) -> None:
    install = """$ErrorActionPreference = "Stop"
$source = $PSScriptRoot
$dest = Join-Path $env:LOCALAPPDATA "Veridix"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Path (Join-Path $source "veridix\\*") -Destination $dest -Recurse -Force
$shortcutDir = Join-Path $env:APPDATA "Microsoft\\Windows\\Start Menu\\Programs"
New-Item -ItemType Directory -Force -Path $shortcutDir | Out-Null
$shortcut = $ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut((Join-Path $shortcutDir "Veridix.lnk"))
$shortcut.TargetPath = Join-Path $dest "veridix.cmd"
$shortcut.WorkingDirectory = $dest
$shortcut.Save()
Set-Content -Path (Join-Path $dest "installed.json") -Value (ConvertTo-Json @{
  installedAt = Get-Date -Format o
  version = $env:VERIDIX_SETUP_VERSION
})
Write-Output ("Veridix installed to " + $dest)
"""
    uninstall = """$dest = Join-Path $env:LOCALAPPDATA "Veridix"
if (Test-Path $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
$shortcut = Join-Path $env:APPDATA "Microsoft\\Windows\\Start Menu\\Programs\\Veridix.lnk"
if (Test-Path $shortcut) { Remove-Item -LiteralPath $shortcut -Force }
"""
    (stage / "install.ps1").write_text(
        install.replace("$env:VERIDIX_SETUP_VERSION", f'"{version}"'),
        encoding="utf-8",
    )
    (stage / "uninstall.ps1").write_text(uninstall, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="build a self-extracting Windows installer for the desktop app"
    )
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--out", default=str(ROOT / "dist-product"))
    parser.add_argument(
        "--payload-dir",
        help="reuse an existing packaged desktop payload",
    )
    parser.add_argument(
        "--with-python",
        default="",
        help="directory of a portable Python runtime to bundle",
    )
    args = parser.parse_args()

    seven_zip = _find_7z()
    stage = ROOT / ".tmp" / "installer-sfx"
    payload_dir = Path(args.payload_dir) if args.payload_dir else (
        ROOT / ".tmp" / "installer-payload"
    )
    if not args.payload_dir:
        _package_payload(payload_dir)
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copytree(payload_dir, stage / "veridix")
    python_runtime = (
        Path(args.with_python)
        if args.with_python
        else ROOT / ".tmp" / "portable-python"
    )
    if python_runtime.exists():
        shutil.copytree(
            python_runtime,
            stage / "veridix" / "python-runtime",
        )
        pth = next(
            (stage / "veridix" / "python-runtime").glob("python*._pth")
        )
        content = pth.read_text(encoding="utf-8")
        if "..\\python" not in content:
            pth.write_text(
                content.rstrip() + "\r\n..\\python\r\n",
                encoding="utf-8",
            )
    _write_install_scripts(stage, args.version)

    archive = stage / "install.7z"
    subprocess.run(
        [
            str(seven_zip),
            "a",
            "-t7z",
            "-y",
            str(archive),
            str(stage / "veridix"),
            str(stage / "install.ps1"),
            str(stage / "uninstall.ps1"),
        ],
        cwd=str(stage),
        check=True,
        timeout=900,
    )
    config = stage / "config.txt"
    config.write_text(
        (
            ";!@Install@!UTF-8!\n"
            'Title="Veridix Setup"\n'
            'RunProgram="powershell -ExecutionPolicy Bypass -File \\"install.ps1\\""\n'
            'GUIMode="2"\n'
            'OverwriteMode="1"\n'
            ";!@InstallEnd@!\n"
        ),
        encoding="utf-8",
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "veridix-setup.exe"
    sfx = Path(r"C:\Program Files\7-Zip\7z.sfx")
    if not sfx.exists():
        sfx = Path(r"C:\Program Files (x86)\7-Zip\7z.sfx")
    with target.open("wb") as handle:
        handle.write(sfx.read_bytes())
        handle.write(config.read_bytes())
        handle.write(archive.read_bytes())

    print(json.dumps({"setup": str(target)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
