from __future__ import annotations

import json
import subprocess
import sys

from services.tool_pack.preflight import (
    _missing_binaries,
    _registry_source,
)


def test_preflight_dry_run_returns_plan() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.tool_pack.preflight",
            "--dry-run",
            "--build",
            "--fetch",
            "--registry",
            "registry.example.test",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert len(payload) == 9
    assert all(item["health"] == "dry_run" for item in payload)
    assert all(item["build"] is True for item in payload)
    assert all(item["fetch"] is True for item in payload)
    assert all(item["registry"] == "registry.example.test" for item in payload)
    assert {item["name"] for item in payload} >= {
        "base",
        "web",
        "vulnscan",
    }


def test_missing_binaries_reports_absent_downloads(tmp_path) -> None:
    missing = _missing_binaries(tmp_path)

    assert set(missing) == {"nuclei.zip", "fscan", "metasploit.deb"}


def test_registry_source_is_pinned_by_digest() -> None:
    source = _registry_source("registry.example.test")

    assert source.startswith(
        "registry.example.test/veridix/veridix-tools@sha256:"
    )
    assert source.endswith(
        "sha256:bf67b075a85eb095689c5bacfe0bbd3d5819e0075abd243685c782fc4c4263ec"
    )
