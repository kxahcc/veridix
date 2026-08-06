#!/usr/bin/env python
"""One-command release gate aggregating every product validation stage.

Stage 1 (required): default acceptance suite.
Stage 2 (optional): real DeepSeek self-healing / memory / graph / worker.
Stage 3 (optional): real six-tool matrix.
Stage 4 (optional): real MCP connectivity.
Stage 5 (external): platform/tool/SSH/OAST/AD/remote gate; limited/pending
                    items do not block the overall release gate.

Use --dry-run to print the plan without running anything.
Use --reuse to aggregate the most recent result files instead of re-running
the stages (useful after a fresh acceptance run or for release snapshots).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def _run(args: list[str], timeout: float) -> dict:
    started = time.time()
    try:
        result = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ},
        )
        return {
            "exit_code": result.returncode,
            "duration": round(time.time() - started, 2),
            "stdout_tail": result.stdout[-1200:],
            "stderr_tail": result.stderr[-800:],
        }
    except subprocess.TimeoutExpired as error:
        return {
            "exit_code": 124,
            "duration": round(time.time() - started, 2),
            "stdout_tail": str(error.stdout or "")[-1200:],
            "stderr_tail": f"timeout after {timeout}s",
        }


def _load_result(name: str) -> dict:
    path = ROOT / "benchmarks" / "results" / name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


REUSE_FILES: list[dict] = [
    {
        "name": "default-acceptance",
        "file": "acceptance-gate-2026-08-06-all.json",
        "overall_key": "overall",
        "required": True,
    },
    {
        "name": "real-self-healing",
        "file": (
            "acceptance-gate-2026-08-05-real-self-healing-real-memory-"
            "real-graph-recovery-worker-recovery.json"
        ),
        "overall_key": "overall",
        "required": True,
    },
    {
        "name": "real-tool-matrix",
        "file": "real-tool-matrix-all-2026-08-06.json",
        "overall_key": "assertion",
        "required": True,
    },
    {
        "name": "mcp-real",
        "file": "acceptance-gate-2026-08-05-mcp-real.json",
        "overall_key": "overall",
        "required": True,
    },
    {
        "name": "external-validation",
        "file": "external-gate-2026-08-06.json",
        "overall_key": "overall",
        "required": False,
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-real", action="store_true")
    parser.add_argument("--skip-tool-matrix", action="store_true")
    parser.add_argument("--skip-mcp", action="store_true")
    parser.add_argument("--skip-external", action="store_true")
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument(
        "--out",
        default=(
            "benchmarks/results/release-gate-"
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        ),
    )
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--build-artifacts", action="store_true")
    parser.add_argument("--artifacts-key", default="")
    parser.add_argument("--artifacts-dry-run", action="store_true")
    parser.add_argument("--verify-artifacts", action="store_true")
    parser.add_argument("--artifacts-public-key", default="")
    args = parser.parse_args()

    artifact_stages: list[dict] = []
    if args.build_artifacts:
        artifact_command = [
            PYTHON,
            "scripts/build_release_artifacts.py",
            "--out",
            ".tmp/release-artifacts",
            "--version",
            args.version,
        ]
        if args.artifacts_dry_run:
            artifact_command.append("--dry-run")
        else:
            if not args.artifacts_key:
                raise SystemExit(
                    "--artifacts-key is required unless --artifacts-dry-run"
                )
            artifact_command += ["--key", args.artifacts_key]
        artifact_stages.append(
            {
                "name": "artifacts",
                "command": artifact_command,
                "timeout": 1800,
                "required": False,
            }
        )
    if args.verify_artifacts:
        if not args.artifacts_public_key:
            raise SystemExit(
                "--artifacts-public-key is required with --verify-artifacts"
            )
        artifact_stages.append(
            {
                "name": "artifact-verify",
                "command": [
                    PYTHON,
                    "scripts/verify_release_artifacts.py",
                    "--airgap",
                    ".tmp/release-artifacts/veridix-airgap.zip",
                    "--public-key",
                    args.artifacts_public_key,
                    "--out",
                    ".tmp/release-verify",
                ],
                "timeout": 1800,
                "required": False,
            }
        )
    if args.reuse and artifact_stages:
        raise SystemExit("--reuse cannot be combined with artifact flags")

    if args.reuse:
        rows: list[dict] = []
        missing: list[str] = []
        failed: list[str] = []
        for stage in REUSE_FILES:
            if args.skip_external and stage["name"] == "external-validation":
                continue
            payload = _load_result(stage["file"])
            if not payload:
                rows.append(
                    {
                        "name": stage["name"],
                        "required": stage["required"],
                        "status": "missing",
                        "file": stage["file"],
                    }
                )
                missing.append(stage["name"])
                continue
            value = str(payload.get(stage["overall_key"]) or "")
            ok = value.lower() in ("passed", "ready")
            rows.append(
                {
                    "name": stage["name"],
                    "required": stage["required"],
                    "status": "passed" if ok else "failed",
                    "file": stage["file"],
                    "overall": value,
                }
            )
            if stage["required"] and not ok:
                failed.append(stage["name"])
        report = {
            "generated_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "suite": "release_gate",
            "mode": "reuse",
            "stages": rows,
            "missing": missing,
            "failed": failed,
            "overall": "passed" if not failed and not missing else "failed",
        }
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not failed and not missing else 1

    stages: list[dict] = [
        {
            "name": "default-acceptance",
            "command": [
                PYTHON,
                "scripts/acceptance_gate.py",
                "--retries",
                "1",
                "--out",
                (
                    "benchmarks/results/release-default-"
                    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
                ),
            ],
            "timeout": 1800,
            "required": True,
        }
    ]
    if not args.skip_real:
        stages.append(
            {
                "name": "real-self-healing",
                "command": [
                    PYTHON,
                    "scripts/acceptance_gate.py",
                    "--suite",
                    "real-self-healing,real-memory,real-graph-recovery,"
                    "worker-recovery",
                    "--retries",
                    "1",
                ],
                "timeout": 2400,
                "required": True,
            }
        )
    if not args.skip_tool_matrix:
        stages.append(
            {
                "name": "real-tool-matrix",
                "command": [
                    PYTHON,
                    "scripts/acceptance_gate.py",
                    "--suite",
                    "real-tool-matrix",
                    "--retries",
                    "0",
                ],
                "timeout": 2400,
                "required": True,
            }
        )
    if not args.skip_mcp:
        stages.append(
            {
                "name": "mcp-real",
                "command": [
                    PYTHON,
                    "scripts/acceptance_gate.py",
                    "--suite",
                    "mcp-real",
                    "--retries",
                    "0",
                ],
                "timeout": 300,
                "required": True,
            }
        )
    if not args.skip_external:
        stages.append(
            {
                "name": "external-validation",
                "command": [
                    PYTHON,
                    "scripts/external_gate.py",
                    "--out",
                    (
                        "benchmarks/results/release-external-"
                        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
                    ),
                ],
                "timeout": 1800,
                "required": False,
            }
        )

    if args.dry_run:
        print(
            json.dumps(
                {
                    "plan": [
                        {
                            "name": stage["name"],
                            "required": stage["required"],
                            "command": " ".join(stage["command"]),
                            "timeout_seconds": stage["timeout"],
                        }
                        for stage in artifact_stages + stages
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    rows: list[dict] = []
    failed: list[str] = []
    for stage in artifact_stages + stages:
        print(f"release gate {stage['name']}", flush=True)
        run = _run(stage["command"], timeout=stage["timeout"])
        ok = run["exit_code"] == 0
        rows.append(
            {
                "name": stage["name"],
                "required": stage["required"],
                "exit_code": run["exit_code"],
                "duration_seconds": run["duration"],
                "stdout_tail": run["stdout_tail"],
                "stderr_tail": run["stderr_tail"],
                "status": "passed" if ok else "failed",
            }
        )
        print(f"release gate {stage['name']}: {'passed' if ok else 'FAILED'}", flush=True)
        if stage["required"] and not ok:
            failed.append(stage["name"])

    report = {
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "suite": "release_gate",
        "stages": rows,
        "failed": failed,
        "overall": "passed" if not failed else "failed",
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
