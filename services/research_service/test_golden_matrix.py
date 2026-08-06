from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from services.agent_runtime.golden import GoldenResult
from services.control_plane.app.contracts import AgentEvent
from services.evidence_service.models import Finding, FindingStatus
from services.research_service.golden_matrix import (
    GoldenMatrixProvider,
    run_golden_matrix,
)
from services.research_service.models import Scenario
from services.research_service.trajectory import compute_metrics


def _baseline(tmp_path: Path) -> Path:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "completion_rate": 0.5,
                "verified_avg": 0.0,
                "verified_runs_rate": 0.5,
                "duplicate_actions_avg": 1.0,
                "cost_avg": 1.0,
            }
        ),
        encoding="utf-8",
    )
    return baseline


def test_golden_matrix_appends_verified_event_and_compares_baseline(
    monkeypatch,
    tmp_path,
) -> None:
    events = [
        AgentEvent(
            event_id="e1",
            event_type="run.started",
            stream_id="run_1",
            run_id="run_1",
            actor="agent-worker",
            sequence=1,
        ),
        AgentEvent(
            event_id="e2",
            event_type="run.succeeded",
            stream_id="run_1",
            run_id="run_1",
            actor="agent-worker",
            sequence=2,
        ),
    ]

    class FakeDriver:
        def __init__(self, **kwargs) -> None:
            pass

        def run(self, spec) -> GoldenResult:
            finding = Finding(
                finding_id="finding_golden",
                target_ref="https://lab.example.test",
                vuln_category="golden",
                endpoint="https://lab.example.test",
                status=FindingStatus.VERIFIED,
            )
            return GoldenResult(
                run_id="matrix_fake",
                status="succeeded",
                events=tuple(events),
                metrics=compute_metrics(events),
                finding=finding,
                evidence_refs=("ev_1",),
                oracle_passed=True,
                harness_digest="h",
                behavior_snapshot_id="b",
            )

    monkeypatch.setattr(
        "services.research_service.golden_matrix.GoldenRunDriver",
        FakeDriver,
    )
    scenario = Scenario(
        scenario_id="s",
        name="s",
        target_ref="https://lab.example.test",
        mode="single",
    )

    report = run_golden_matrix(
        [
            GoldenMatrixProvider(
                provider_id="fake",
                model="fixture-model",
                endpoint="http://127.0.0.1:1/v1",
            )
        ],
        scenario,
        baseline_path=str(_baseline(tmp_path)),
        runs=1,
    )

    row = report.rows[0]
    assert row.aggregate["completion_rate"] == 1.0
    assert row.aggregate["verified_avg"] > 0.0
    assert row.meets_baseline is True
    assert row.harness_digest == "h"
    assert row.behavior_snapshot_id == "b"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_http(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"endpoint did not become ready: {url}")


@pytest.mark.integration
def test_golden_matrix_against_local_lab_provider(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.lab_provider.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        _wait_http(f"http://127.0.0.1:{port}/healthz")
        scenario = Scenario(
            scenario_id="web-idor-001",
            name="Web IDOR role mutation",
            target_ref="https://lab.example.test",
            expected_findings=("finding_authz_admin",),
            max_turns=5,
            mode="single",
        )

        report = run_golden_matrix(
            [
                GoldenMatrixProvider(
                    provider_id="lab",
                    model="veridix-lab-flash",
                    endpoint=f"http://127.0.0.1:{port}/v1",
                )
            ],
            scenario,
            baseline_path=str(_baseline(tmp_path)),
            runs=1,
        )

        row = report.rows[0]
        assert row.aggregate["completion_rate"] == 1.0
        assert row.aggregate["verified_avg"] > 0.0
        assert row.meets_baseline is True
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
