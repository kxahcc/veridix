from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="package the desktop product")
    parser.add_argument("--out", default="dist-product")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--zip", action="store_true")
    parser.add_argument("--sign", action="store_true")
    parser.add_argument("--zip-only", action="store_true")
    args = parser.parse_args()

    if not args.skip_build:
        subprocess.run(["npm.cmd", "run", "build"], cwd=str(ROOT), check=True)

    out = Path(args.out)
    if args.zip_only:
        _finalize(out, args)
        print(out)
        return 0
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    _copy_tree(ROOT / "apps/cli/dist", out / "cli")
    _copy_tree(ROOT / "apps/tui/dist", out / "tui")
    _copy_tree(ROOT / "apps/web/dist", out / "web")
    _copy_tree(ROOT / "services", out / "python/services")
    _copy_tree(ROOT / "runners", out / "python/runners")
    for name in ("deploy", "knowledge", "skills"):
        if (ROOT / name).exists():
            _copy_tree(ROOT / name, out / name)
    (out / "cli/package.json").write_text(
        json.dumps(
            {"name": "@veridix/cli", "version": args.version, "type": "module"}
        ),
        encoding="utf-8",
    )
    (out / "tui/package.json").write_text(
        json.dumps(
            {"name": "@veridix/tui", "version": args.version, "type": "module"}
        ),
        encoding="utf-8",
    )
    (out / "package.json").write_text(
        json.dumps(
            {
                "name": "veridix-desktop",
                "version": args.version,
                "private": True,
                "workspaces": ["cli", "tui"],
            }
        ),
        encoding="utf-8",
    )

    (out / "veridix.cmd").write_text(
        '@echo off\r\n'
        '@cd /d "%~dp0"\r\n'
        'if exist "%~dp0veridix.exe" (\r\n'
        '  "%~dp0veridix.exe" %*\r\n'
        '  exit /b %errorlevel%\r\n'
        ')\r\n'
        'set "VERIDIX_ROOT=%~dp0"\r\n'
        'set "PYTHONPATH=%~dp0python"\r\n'
        'node "%~dp0cli\\index.js" %*\r\n',
        encoding="ascii",
    )
    (out / "veridix-tui.cmd").write_text(
        '@echo off\r\n'
        '@cd /d "%~dp0"\r\n'
        'if exist "%~dp0veridix-tui.exe" (\r\n'
        '  "%~dp0veridix-tui.exe" %*\r\n'
        '  exit /b %errorlevel%\r\n'
        ')\r\n'
        'set "VERIDIX_ROOT=%~dp0"\r\n'
        'set "PYTHONPATH=%~dp0python"\r\n'
        'node "%~dp0tui\\index.js" %*\r\n',
        encoding="ascii",
    )
    (out / "veridix-web.cmd").write_text(
        '@echo off\r\n'
        'set "PYTHONPATH=%~dp0python"\r\n'
        'if exist "%~dp0python-runtime\\python.exe" (\r\n'
        '  "%~dp0python-runtime\\python.exe" "%~dp0desktop_server.py" --web-dir "%~dp0web" --control-port 8787 --web-port 8788\r\n'
        ') else (\r\n'
        '  python "%~dp0desktop_server.py" --web-dir "%~dp0web" --control-port 8787 --web-port 8788\r\n'
        ')\r\n',
        encoding="ascii",
    )
    shutil.copy2(ROOT / "scripts/desktop_server.py", out / "desktop_server.py")
    suffix = ".exe" if os.name == "nt" else ""
    for exe in (f"veridix{suffix}", f"veridix-tui{suffix}"):
        source = ROOT / "dist-product" / exe
        if source.exists():
            shutil.copy2(source, out / exe)
    (out / "VERSION").write_text(args.version + "\n", encoding="ascii")

    _finalize(out, args)
    print(out)
    return 0


def _finalize(out: Path, args) -> None:
    manifest = _build_manifest(out, args.version)
    signature = None
    public_key = None
    if args.sign:
        from services.release_service.signing import generate_keypair, sign_bytes

        private_key, public_key = generate_keypair()
        manifest["public_key"] = public_key
        manifest_bytes = json.dumps(
            manifest, indent=2, ensure_ascii=True, sort_keys=True
        ).encode("utf-8")
        signature = sign_bytes(manifest_bytes, private_key)
        (out / "manifest.sig").write_text(signature, encoding="ascii")
        (out / "manifest.json").write_bytes(manifest_bytes)
    else:
        (out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
    if args.zip:
        _write_zip(out)


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target)


def _build_manifest(out: Path, version: str) -> dict:
    files: dict[str, str] = {}
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        rel = path.relative_to(out).as_posix()
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "product": "veridix",
        "version": version,
        "platforms": ["windows"],
        "files": files,
    }


def _write_zip(out: Path) -> None:
    archive = out.parent / "veridix-desktop.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(out.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(out.parent).as_posix())


if __name__ == "__main__":
    raise SystemExit(main())
