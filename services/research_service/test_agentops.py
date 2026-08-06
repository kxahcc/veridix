from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from services.control_plane.app.contracts import AgentEvent
from services.research_service.agentops import (
    AgentOpsRegression,
    aggregate_trajectories,
    export_trajectory,
    harness_loop_graph_metrics,
)
from services.research_service.behaviors import snapshot_from_components
from services.research_service.models import Trajectory
from services.research_service.trajectory import compute_metrics


def _events(run_id: str = "run_1") -> list[AgentEvent]:
    return [
        AgentEvent(
            event_id="e1",
            event_type="run.started",
            stream_id=run_id,
            run_id=run_id,
            actor="agent-worker",
            sequence=1,
            payload={"behavior_snapshot": "b1"},
        ),
        AgentEvent(
            event_id="e2",
            event_type="model.turn.started",
            stream_id=run_id,
            run_id=run_id,
            actor="agent-worker",
            sequence=2,
            payload={"turn": 1},
        ),
        AgentEvent(
            event_id="e3",
            event_type="finding.verified",
            stream_id=run_id,
            run_id=run_id,
            actor="agent-worker",
            sequence=3,
            payload={"finding_id": "finding_authz_admin"},
        ),
        AgentEvent(
            event_id="e4",
            event_type="run.succeeded",
            stream_id=run_id,
            run_id=run_id,
            actor="agent-worker",
            sequence=4,
        ),
    ]


def test_export_trajectory_roundtrip(tmp_path) -> None:
    events = _events()
    result = export_trajectory(
        events,
        run_id="run_1",
        out_path=tmp_path / "trajectory.json",
    )

    payload = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))
    assert result["event_count"] == 4
    assert payload["metrics"]["loop_completion"] == 1.0
    assert payload["events"][0]["event_type"] == "run.started"


def test_harness_loop_graph_metrics() -> None:
    metrics = harness_loop_graph_metrics(_events())

    assert metrics["loop_iterations"] == 1
    assert metrics["graph_nodes"] == 0
    assert metrics["harness_snapshots"] == 1


def test_agentops_regression_detects_snapshot_and_metric_changes() -> None:
    baseline_snapshot = snapshot_from_components(
        snapshot_id="s1",
        config={"provider": "remote"},
        harness={"tools": ["proxy.list"]},
        provider="remote",
    )
    current_snapshot = snapshot_from_components(
        snapshot_id="s1",
        config={"provider": "remote"},
        harness={"tools": ["proxy.list", "evidence.replay"]},
        provider="remote",
    )
    trajectories = [
        Trajectory(
            scenario_id="web-idor-001",
            run_id="run_1",
            events=tuple(_events()),
            metrics=compute_metrics(_events()),
        )
    ]

    report = AgentOpsRegression(
        {
            "completion_rate": 1.0,
            "verified_runs_rate": 1.0,
            "duplicate_actions_avg": 0.0,
            "cost_avg": 0.0,
        },
        baseline_snapshot,
    ).evaluate(trajectories, current_snapshot)

    assert report["snapshot_diff"] == ["harness_digest"]
    assert report["regressed"] is True
    assert report["trajectory_count"] == 1
    assert aggregate_trajectories(trajectories)["completion_rate"] == 1.0


def test_agentops_cli_exports_trajectories(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "baseline_aggregate": {"completion_rate": 1.0},
                "baseline_snapshot": {
                    "snapshot_id": "s1",
                    "config_hash": "a",
                    "harness_digest": "b",
                    "provider": "remote",
                },
                "current_snapshot": {
                    "snapshot_id": "s1",
                    "config_hash": "a",
                    "harness_digest": "c",
                    "provider": "remote",
                },
                "runs": [
                    {
                        "run_id": "run_1",
                        "scenario_id": "web-idor-001",
                        "events": [event.model_dump(mode="json") for event in _events()],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.research_service.agentops_cli",
            "--input",
            str(input_path),
            "--out",
            str(tmp_path / "report.json"),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["regressed"] is True
    assert report["harness_loop_graph"][0]["loop_iterations"] == 1
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "run_1.json").exists()
