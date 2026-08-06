#!/usr/bin/env python
"""Run the repeatable local acceptance gate for Veridix.

The gate groups the product baselines into one command and one report:

- unit-core: knowledge, mission graph, control plane tests
- memory-tools: agent-visible project memory tools and context refresh
- clean-install: fresh npm ci + build + python compileall + isolated stack
  startup with Vite control-plane URL injection
- retrieval-scope: target_ref / time-window retrieval filtering and FTS migration
- frontend: Web build plus CLI and TUI test suites
- tui-long-interaction: long TUI session navigation against a mocked control plane
- stress-long-run: long tool loop with context trimming and durable transcript
- stress-checkpoint-resume: pause/resume a long run with a fresh broker
- three-surface: Web/CLI/TUI unified acceptance audit
- system-smoke: unified Compose health and storage/tool smoke
- local-real-smoke: local OAST and SSH real smokes with automatic cleanup
- mcp-real: real stdio MCP connectivity (veridix-local + external Filesystem)
- real-tool-matrix: six real scanner missions (zap/nmap/nikto/sqlmap/nuclei/
  fscan) through DeepSeek + Docker runner; requires DEEPSEEK_API_KEY
- external-validation: aggregated platform/tool/SSH/OAST/AD/remote gate
- memory-impact: optional real Docker + DeepSeek clean-vs-memory comparison
- skill-selection: real semantic skill retrieval over ten task profiles
- profile-context: deterministic per-loop override benchmark
- preset-fixtures: deterministic contract gate for all presets
- preset-external: deterministic AD/cloud preset integrity gate

Every step supports bounded retries so Windows localhost flakiness does not
turn into a false product regression.

Usage:
  python scripts/acceptance_gate.py --suite unit-core,frontend
  python scripts/acceptance_gate.py            # all suites
  python scripts/acceptance_gate.py --retries 2
  python scripts/acceptance_gate.py --real      # adds real-model self-healing
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
NPM = "npm.cmd" if os.name == "nt" else "npm"
PYTHON = sys.executable
DEFAULT_SUITES = (
    "unit-core",
    "memory-tools",
    "clean-install",
    "frontend",
    "tui-long-interaction",
    "stress-long-run",
    "stress-checkpoint-resume",
    "three-surface",
    "system-smoke",
    "profile-context",
    "preset-fixtures",
    "preset-external",
)


def _run(
    args: list[str],
    *,
    env: dict | None = None,
    timeout: float = 600.0,
) -> dict:
    started = time.time()
    try:
        result = subprocess.run(
            args,
            cwd=str(ROOT),
            env=env if env is not None else {**os.environ},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
            "duration": round(time.time() - started, 2),
        }
    except subprocess.TimeoutExpired as error:
        return {
            "exit_code": 124,
            "stdout": str(error.stdout or "")[-2000:],
            "stderr": f"timeout after {timeout}s",
            "duration": round(time.time() - started, 2),
        }


def _step(
    name: str,
    func,
    *,
    retries: int = 1,
    timeout: float = 600.0,
) -> dict:
    last: dict = {}
    for attempt in range(max(1, retries + 1)):
        started = time.time()
        print(f"gate {name} attempt {attempt + 1}", flush=True)
        last = func(timeout=timeout)
        last["attempt"] = attempt + 1
        last["duration"] = round(time.time() - started, 2)
        if last["exit_code"] == 0:
            break
        if attempt < retries:
            print(f"gate {name} failed, retrying", flush=True)
    return {
        "name": name,
        "status": "passed" if last["exit_code"] == 0 else "failed",
        "exit_code": last["exit_code"],
        "attempt": last.get("attempt", 1),
        "duration": last.get("duration", 0.0),
        "stdout_tail": last.get("stdout", ""),
        "stderr_tail": last.get("stderr", ""),
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        default=",".join(DEFAULT_SUITES),
        help="comma-separated suite names",
    )
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument(
        "--real",
        action="store_true",
        help=(
            "add real-model self-healing and graph-recovery gates "
            "(requires DeepSeek/worker)"
        ),
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="add the deep Web interaction audit (MissionSetup + RunCockpit + Evidence)",
    )
    parser.add_argument(
        "--out",
        default=(
            "benchmarks/results/acceptance-gate-"
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
            "-{suite}.json"
        ),
    )
    args = parser.parse_args()

    suites = [item.strip() for item in args.suite.split(",") if item.strip()]
    if args.real and "real-self-healing" not in suites:
        suites.append("real-self-healing")
    if args.real and "real-memory" not in suites:
        suites.append("real-memory")
    if args.real and "real-graph-recovery" not in suites:
        suites.append("real-graph-recovery")
    if args.real and "worker-recovery" not in suites:
        suites.append("worker-recovery")
    if args.deep and "deep-interaction" not in suites:
        suites.append("deep-interaction")
    if args.deep and "tui-smoke" not in suites:
        suites.append("tui-smoke")
    if suites == list(DEFAULT_SUITES):
        suite_stub = "all"
    elif set(DEFAULT_SUITES).issubset(set(suites)):
        extras = [
            suite
            for suite in suites
            if suite not in DEFAULT_SUITES
        ]
        suite_stub = "all-real" if extras else "all"
    else:
        suite_stub = "-".join(suites)
    if "{suite}" in args.out:
        args.out = args.out.format(suite=suite_stub)
    steps: list[dict] = []

    if "unit-core" in suites:
        steps.append(
            _step(
                "unit-core",
                lambda timeout: _run(
                    [
                        PYTHON,
                        "-m",
                        "pytest",
                        "services/knowledge_service",
                        "services/mission_orchestrator",
                        "services/control_plane",
                        "-q",
                    ],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=600,
            )
        )
    if "memory-tools" in suites:
        steps.append(
            _step(
                "memory-tools",
                lambda timeout: _run(
                    [
                        PYTHON,
                        "-m",
                        "pytest",
                        "services/agent_runtime/test_memory_tools.py",
                        "-q",
                    ],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=60,
            )
        )
    if "clean-install" in suites:
        steps.append(
            _step(
                "clean-install",
                lambda timeout: _run(
                    [PYTHON, "scripts/clean_install_smoke.py", "--up"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=600,
            )
        )
    if "retrieval-scope" in suites:
        steps.append(
            _step(
                "retrieval-scope",
                lambda timeout: _run(
                    [
                        PYTHON,
                        "-m",
                        "pytest",
                        "services/knowledge_service/test_hybrid_retrieval.py",
                        "services/knowledge_service/test_knowledge.py",
                        "-q",
                    ],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=120,
            )
        )
    if "frontend" in suites:
        def frontend_check(timeout: float) -> dict:
            commands = [
                [NPM, "run", "build", "-w", "@veridix/web"],
                [NPM, "run", "test", "-w", "@veridix/cli"],
                [NPM, "run", "test", "-w", "@veridix/tui"],
            ]
            last: dict = {"exit_code": 0, "stdout": "", "stderr": ""}
            for command in commands:
                last = _run(command, timeout=timeout)
                if last["exit_code"] != 0:
                    break
            return last

        steps.append(
            _step(
                "frontend",
                frontend_check,
                retries=args.retries,
                timeout=240,
            )
        )
    if "tui-long-interaction" in suites:
        steps.append(
            _step(
                "tui-long-interaction",
                lambda timeout: _run(
                    [PYTHON, "scripts/tui_long_interaction.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=180,
            )
        )
    if "stress-long-run" in suites:
        steps.append(
            _step(
                "stress-long-run",
                lambda timeout: _run(
                    [PYTHON, "scripts/stress_long_run.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=180,
            )
        )
    if "stress-checkpoint-resume" in suites:
        steps.append(
            _step(
                "stress-checkpoint-resume",
                lambda timeout: _run(
                    [PYTHON, "scripts/stress_checkpoint_resume.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=180,
            )
        )
    if "three-surface" in suites:
        steps.append(
            _step(
                "three-surface",
                lambda timeout: _run(
                    [PYTHON, "scripts/three_surface_audit.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=1500,
            )
        )
    if "system-smoke" in suites:
        steps.append(
            _step(
                "system-smoke",
                lambda timeout: _run(
                    [PYTHON, "scripts/env_up.py", "--no-stack", "--smoke"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=240,
            )
        )
    if "local-real-smoke" in suites:
        steps.append(
            _step(
                "oast-local-smoke",
                lambda timeout: _run(
                    [PYTHON, "scripts/oast_local_smoke.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=120,
            )
        )
        steps.append(
            _step(
                "ssh-local-smoke",
                lambda timeout: _run(
                    [PYTHON, "scripts/ssh_real_smoke.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=300,
            )
        )
    if "mcp-real" in suites:
        steps.append(
            _step(
                "mcp-real",
                lambda timeout: _run(
                    [PYTHON, "scripts/mcp_real_smoke.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=240,
            )
        )
    if "real-tool-matrix" in suites:
        steps.append(
            _step(
                "real-tool-matrix",
                lambda timeout: _run(
                    [
                        PYTHON,
                        "scripts/run_real_provider_gates.py",
                        "--provider-endpoint",
                        os.environ.get(
                            "VERIDIX_PROVIDER_ENDPOINT",
                            "https://api.deepseek.com/v1",
                        ),
                        "--model",
                        os.environ.get(
                            "VERIDIX_PROVIDER_MODEL",
                            "deepseek-v4-flash",
                        ),
                        "--api-key-ref",
                        "env:DEEPSEEK_API_KEY",
                        "--scenarios",
                        "zap,nmap,nikto,sqlmap,nuclei,fscan",
                        "--timeout-seconds",
                        "600",
                        "--out",
                        (
                            "benchmarks/results/acceptance-real-tool-matrix-"
                            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
                        ),
                    ],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=2400,
            )
        )
    if "external-validation" in suites:
        steps.append(
            _step(
                "external-validation",
                lambda timeout: _run(
                    [
                        PYTHON,
                        "scripts/external_gate.py",
                        "--out",
                        (
                            "benchmarks/results/acceptance-external-"
                            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
                        ),
                    ],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=1800,
            )
        )
    if "memory-impact" in suites:
        steps.append(
            _step(
                "memory-impact",
                lambda timeout: _run(
                    [
                        PYTHON,
                        "scripts/bench_memory_impact.py",
                        "--provider-endpoint",
                        os.environ.get(
                            "VERIDIX_PROVIDER_ENDPOINT",
                            "https://api.deepseek.com/v1",
                        ),
                        "--model",
                        os.environ.get(
                            "VERIDIX_PROVIDER_MODEL",
                            "deepseek-v4-flash",
                        ),
                        "--api-key-ref",
                        "env:DEEPSEEK_API_KEY",
                        "--timeout-seconds",
                        "420",
                    ],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=900,
            )
        )
    if "real-self-healing" in suites:
        def real_check(timeout: float) -> dict:
            return _run(
                [
                    PYTHON,
                    "scripts/real_self_healing_gate.py",
                    "--provider-endpoint",
                    os.environ.get(
                        "VERIDIX_PROVIDER_ENDPOINT",
                        "https://api.deepseek.com/v1",
                    ),
                    "--model",
                    os.environ.get(
                        "VERIDIX_PROVIDER_MODEL",
                        "deepseek-v4-flash",
                    ),
                    "--timeout-seconds",
                    "360",
                ],
                timeout=timeout,
            )

        steps.append(
            _step(
                "real-self-healing",
                real_check,
                retries=args.retries,
                timeout=420,
            )
        )
    if "real-memory" in suites:
        steps.append(
            _step(
                "real-memory",
                lambda timeout: _run(
                    [
                        PYTHON,
                        "scripts/real_memory_gate.py",
                        "--provider-endpoint",
                        os.environ.get(
                            "VERIDIX_PROVIDER_ENDPOINT",
                            "https://api.deepseek.com/v1",
                        ),
                        "--model",
                        os.environ.get(
                            "VERIDIX_PROVIDER_MODEL",
                            "deepseek-v4-flash",
                        ),
                        "--api-key-ref",
                        "env:DEEPSEEK_API_KEY",
                        "--attempts",
                        "1",
                        "--timeout-seconds",
                        "360",
                    ],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=420,
            )
        )
    if "real-graph-recovery" in suites:
        def graph_recovery_check(timeout: float) -> dict:
            return _run(
                [
                    PYTHON,
                    "scripts/real_graph_recovery_gate.py",
                    "--provider-endpoint",
                    os.environ.get(
                        "VERIDIX_PROVIDER_ENDPOINT",
                        "https://api.deepseek.com/v1",
                    ),
                    "--model",
                    os.environ.get(
                        "VERIDIX_PROVIDER_MODEL",
                        "deepseek-v4-flash",
                    ),
                    "--timeout-seconds",
                    "420",
                ],
                timeout=timeout,
            )

        steps.append(
            _step(
                "real-graph-recovery",
                graph_recovery_check,
                retries=args.retries,
                timeout=480,
            )
        )
    if "deep-interaction" in suites:
        steps.append(
            _step(
                "deep-interaction",
                lambda timeout: _run(
                    [PYTHON, "scripts/deep_interaction_audit.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=120,
            )
        )
    if "worker-recovery" in suites:
        def worker_recovery_check(timeout: float) -> dict:
            return _run(
                [
                    PYTHON,
                    "scripts/worker_recovery_gate.py",
                    "--provider-endpoint",
                    os.environ.get(
                        "VERIDIX_PROVIDER_ENDPOINT",
                        "https://api.deepseek.com/v1",
                    ),
                    "--model",
                    os.environ.get(
                        "VERIDIX_PROVIDER_MODEL",
                        "deepseek-v4-flash",
                    ),
                    "--api-key-ref",
                    "env:DEEPSEEK_API_KEY",
                ],
                timeout=timeout,
            )

        steps.append(
            _step(
                "worker-recovery",
                worker_recovery_check,
                retries=args.retries,
                timeout=480,
            )
        )
    if "tui-smoke" in suites:
        steps.append(
            _step(
                "tui-smoke",
                lambda timeout: _run(
                    [PYTHON, "scripts/tui_smoke.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=60,
            )
        )
    if "rag-quality" in suites:
        steps.append(
            _step(
                "rag-quality",
                lambda timeout: _run(
                    [PYTHON, "scripts/rag_quality_gate.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=120,
            )
        )
    if "skill-selection" in suites:
        steps.append(
            _step(
                "skill-selection",
                lambda timeout: _run(
                    [PYTHON, "scripts/skill_selection_gate.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=180,
            )
        )
    if "profile-context" in suites:
        steps.append(
            _step(
                "profile-context",
                lambda timeout: _run(
                    [PYTHON, "scripts/bench_loop_profile_context.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=60,
            )
        )
    if "preset-fixtures" in suites:
        steps.append(
            _step(
                "preset-fixtures",
                lambda timeout: _run(
                    [PYTHON, "scripts/bench_preset_fixtures.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=60,
            )
        )
    if "preset-external" in suites:
        steps.append(
            _step(
                "preset-external",
                lambda timeout: _run(
                    [PYTHON, "scripts/bench_preset_external.py"],
                    timeout=timeout,
                ),
                retries=args.retries,
                timeout=60,
            )
        )

    overall = (
        "passed"
        if steps and all(step["status"] == "passed" for step in steps)
        else "failed"
    )
    report = {
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "overall": overall,
        "steps": steps,
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if overall == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
