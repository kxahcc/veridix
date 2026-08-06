from __future__ import annotations

import json
import subprocess
import sys


def test_bench_cli_dry_run_returns_plan() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.research_service.bench_cli",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["dry_run"] is True
    assert payload["plan"][0]["suite"] == "rag"


def test_bench_cli_role_suite_runs_comparison() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.research_service.bench_cli",
            "--suite",
            "role",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["recommendation"] in ("single", "graph")
    assert payload["single"]["verified"] is True
    assert payload["graph"]["verified"] is True
