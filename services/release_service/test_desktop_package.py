from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_desktop_package_contains_three_surfaces(tmp_path) -> None:
    out = tmp_path / "product"
    subprocess.run(
        [
            sys.executable,
            "scripts/package_desktop.py",
            "--out",
            str(out),
            "--version",
            "0.1.0-test",
            "--skip-build",
        ],
        cwd=str(ROOT),
        check=True,
        capture_output=True,
    )

    assert (out / "veridix.cmd").exists()
    assert (out / "veridix-tui.cmd").exists()
    assert (out / "veridix-web.cmd").exists()
    assert (out / "web" / "index.html").exists()
    assert (out / "cli" / "index.js").exists()
    assert (out / "tui" / "index.js").exists()
    assert (
        out / "python" / "services" / "control_plane" / "app" / "main.py"
    ).exists()
    assert (
        out / "python" / "runners" / "web" / "caido_connector.py"
    ).exists()
    assert (out / "deploy" / "toolpacks" / "web.json").exists()
    assert (out / "knowledge" / "builtin" / "web-security-basics.json").exists()
    assert (out / "package.json").exists()
    assert "PYTHONPATH" in (out / "veridix.cmd").read_text(encoding="ascii")
    assert "VERIDIX_ROOT" in (out / "veridix.cmd").read_text(encoding="ascii")
    assert "PYTHONPATH" in (out / "veridix-web.cmd").read_text(encoding="ascii")
    assert (out / "VERSION").read_text().strip() == "0.1.0-test"

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["product"] == "veridix"
    assert "web/index.html" in manifest["files"]
    assert "cli/index.js" in manifest["files"]


def test_desktop_package_zip_is_signed(tmp_path) -> None:
    out = tmp_path / "product"
    subprocess.run(
        [
            sys.executable,
            "scripts/package_desktop.py",
            "--out",
            str(out),
            "--version",
            "0.1.0-test",
            "--skip-build",
            "--zip",
            "--sign",
        ],
        cwd=str(ROOT),
        check=True,
        capture_output=True,
    )
    archive = out.parent / "veridix-desktop.zip"

    assert archive.exists()
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert any(name.endswith("veridix.cmd") for name in names)
        assert any(name.endswith("manifest.json") for name in names)
        assert any(name.endswith("manifest.sig") for name in names)
        manifest_path = next(name for name in names if name.endswith("manifest.json"))
        sig_path = next(name for name in names if name.endswith("manifest.sig"))
        manifest_bytes = bundle.read(manifest_path)
        manifest = json.loads(manifest_bytes)
        signature = bundle.read(sig_path).decode("ascii")

    from services.release_service.signing import verify_bytes

    assert verify_bytes(
        manifest_bytes,
        signature,
        manifest["public_key"],
    ) is True
