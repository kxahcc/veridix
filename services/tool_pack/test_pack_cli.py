from __future__ import annotations

import json
import subprocess
import sys


def test_pack_cli_list_and_dry_run_install() -> None:
    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.tool_pack.pack_cli",
            "list",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    packs = json.loads(listed.stdout)
    names = {pack["name"] for pack in packs}

    assert {"base", "web", "vulnscan"} <= names

    dry = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.tool_pack.pack_cli",
            "install",
            "vulnscan",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(dry.stdout)
    assert payload["dry_run"] is True
    assert payload["name"] == "vulnscan"


def test_pack_cli_export_dry_run_returns_plan(tmp_path) -> None:
    out = tmp_path / "tools.tar.gz"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.tool_pack.pack_cli",
            "export",
            "--out",
            str(out),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["dry_run"] is True
    assert payload["image"] == "veridix-tools:full"


def test_pack_cli_airgap_dry_run_returns_plan(tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.tool_pack.pack_cli",
            "airgap",
            "--out",
            str(tmp_path / "airgap.zip"),
            "--desktop-zip",
            str(tmp_path / "desktop.zip"),
            "--tools-tar",
            str(tmp_path / "tools.tar.gz"),
            "--key",
            "abc",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["dry_run"] is True
    assert payload["action"] == "airgap"
