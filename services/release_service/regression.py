from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


PYTEST_SUMMARY = re.compile(
    r"(?:(\d+) failed, )?(\d+) passed"
    r"(?:, (\d+) failed)?(?:, (\d+) error)?(?:, (\d+) skipped)?"
)


def parse_pytest_summary(output: str) -> dict:
    match = PYTEST_SUMMARY.search(output)
    if match is None:
        return {
            "status": "unknown",
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "detail": "no pytest summary found",
        }
    leading_failed = int(match.group(1) or 0)
    passed = int(match.group(2))
    failed = leading_failed + int(match.group(3) or 0)
    errors = int(match.group(4) or 0)
    skipped = int(match.group(5) or 0)
    return {
        "status": "passed" if failed == 0 and errors == 0 else "failed",
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "detail": (
            f"{passed} passed, {failed} failed, "
            f"{errors} error, {skipped} skipped"
        ),
    }


def _run(command: list[str], cwd: Path, timeout: int) -> dict:
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        output = f"{proc.stdout}\n{proc.stderr}".strip()
        return {
            "exit_code": proc.returncode,
            "output": output[-4000:],
        }
    except subprocess.TimeoutExpired as error:
        return {
            "exit_code": None,
            "output": f"timeout after {timeout}s; {error.stdout or ''}",
        }


def _run_with_retry(
    command: list[str],
    cwd: Path,
    timeout: int,
    *,
    retries: int = 2,
) -> dict:
    result = _run(command, cwd, timeout)
    for _ in range(retries):
        if result["exit_code"] == 0:
            break
        result = _run(command, cwd, timeout)
    return result


def run_regressions(
    root: str | Path,
    python: str,
    *,
    timeout: int = 600,
    include_web_faults: bool = False,
    include_e2e: bool = False,
    broad: bool = False,
    targets: list[str] | None = None,
) -> dict:
    root_path = Path(root)
    basetemp = root_path / ".pytest_tmp" / "release"
    if basetemp.exists():
        shutil.rmtree(basetemp, ignore_errors=True)
    basetemp.mkdir(parents=True, exist_ok=True)
    python_summaries: list[dict] = []
    python_outputs: list[str] = []
    python_exit_codes: list[int | None] = []
    if broad:
        python_targets = ["services/control_plane", "services/agent_runtime"]
    else:
        python_targets = targets or [
            "services/control_plane/test_product_models.py",
            "services/agent_runtime/test_storage_defaults.py",
            "services/agent_runtime/test_storage_provisioning.py",
            "services/agent_runtime/test_roles.py",
            "services/agent_runtime/test_loop_engineering.py",
            "services/agent_runtime/test_loop_adapters.py",
            "services/agent_runtime/test_security_loops.py",
            "services/agent_runtime/test_tool_pack.py",
            "services/knowledge_service/test_hybrid_retrieval.py",
            "services/knowledge_service/test_knowledge.py",
            "services/knowledge_service/test_skill_conformance.py",
            "services/knowledge_service/test_vector_backends.py",
            "services/mission_orchestrator/test_graph.py",
            "services/mission_orchestrator/test_graph_store.py",
        ]
    for target in python_targets:
        result = _run_with_retry(
            [
                python,
                "-m",
                "pytest",
                target,
                "-q",
                "--tb=no",
                "--basetemp",
                str(basetemp),
            ],
            root_path,
            timeout,
        )
        python_exit_codes.append(result["exit_code"])
        python_outputs.append(result["output"])
        python_summaries.append(parse_pytest_summary(result["output"]))
    python_summary = {
        "status": (
            "passed"
            if all(
                item["status"] == "passed" for item in python_summaries
            )
            else "failed"
        ),
        "passed": sum(item["passed"] for item in python_summaries),
        "failed": sum(item["failed"] for item in python_summaries),
        "errors": sum(item["errors"] for item in python_summaries),
        "skipped": sum(item["skipped"] for item in python_summaries),
        "detail": (
            f"{sum(item['passed'] for item in python_summaries)} passed, "
            f"{sum(item['failed'] for item in python_summaries)} failed, "
            f"{sum(item['errors'] for item in python_summaries)} error, "
            f"{sum(item['skipped'] for item in python_summaries)} skipped"
        ),
    }
    npm = "npm.cmd" if os.name == "nt" else "npm"
    typescript_result = _run_with_retry(
        [npm, "test"],
        root_path,
        timeout,
    )
    typescript_status = (
        "passed"
        if typescript_result["exit_code"] == 0
        else "failed"
    )
    if include_e2e:
        e2e_result = _run_with_retry(
            [
                python,
                "-m",
                "pytest",
                "tests/e2e/test_first_usable_loop.py",
                "-q",
                "--tb=no",
                "--basetemp",
                str(basetemp),
            ],
            root_path,
            timeout,
        )
        e2e_summary = parse_pytest_summary(e2e_result["output"])
        e2e_block = {
            **e2e_summary,
            "exit_code": e2e_result["exit_code"],
            "output_tail": e2e_result["output"][-1500:],
        }
    else:
        e2e_block = {
            "status": "skipped",
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "detail": "e2e disabled; run with --include-e2e or CI e2e job",
            "exit_code": None,
            "output_tail": "",
        }
    result = {
        "python": {
            **python_summary,
            "exit_code": (
                0
                if all(code == 0 for code in python_exit_codes)
                else 1
            ),
            "output_tail": "\n".join(python_outputs)[-2000:],
        },
        "typescript": {
            "status": typescript_status,
            "exit_code": typescript_result["exit_code"],
            "detail": (
                "npm test passed"
                if typescript_result["exit_code"] == 0
                else "npm test failed"
            ),
            "output_tail": typescript_result["output"][-1500:],
        },
        "e2e": e2e_block,
    }
    if include_web_faults:
        web_faults_result = _run_with_retry(
            [
                python,
                "-m",
                "pytest",
                "tests/e2e/test_web_faults.py",
                "tests/e2e/test_web_start_run.py",
                "tests/e2e/test_web_worker_loop.py",
                "tests/e2e/test_agent_browser_loop.py",
                "-q",
                "--tb=no",
            ],
            root_path,
            timeout,
        )
        web_faults_summary = parse_pytest_summary(
            web_faults_result["output"]
        )
        result["web_faults"] = {
            **web_faults_summary,
            "exit_code": web_faults_result["exit_code"],
            "output_tail": web_faults_result["output"][-1500:],
        }
    return result
