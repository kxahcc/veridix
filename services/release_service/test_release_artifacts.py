from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_artifacts_dry_run_returns_plan() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_release_artifacts.py"),
            "--dry-run",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["action"] == "release_artifacts"
    assert payload["dry_run"] is True
    assert "assemble signed airgap bundle" in payload["steps"]


def test_release_gate_exposes_artifacts_flags() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_gate.py"),
            "--help",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )

    assert "--build-artifacts" in result.stdout
    assert "--artifacts-dry-run" in result.stdout
    assert "--verify-artifacts" in result.stdout
    assert "--artifacts-public-key" in result.stdout


def test_verify_release_artifacts_dry_run() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "verify_release_artifacts.py"),
            "--airgap",
            "dist-product/verify.zip",
            "--public-key",
            "abc",
            "--dry-run",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["action"] == "verify_release_artifacts"
    assert payload["dry_run"] is True
