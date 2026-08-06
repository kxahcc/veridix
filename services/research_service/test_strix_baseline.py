from __future__ import annotations

import json

from services.research_service.strix_baseline import (
    build_baseline_report,
    load_run_metrics,
)


def test_load_run_metrics_normalizes_strix_run(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "strix_1",
                "status": "running",
                "llm_usage": {
                    "requests": 3,
                    "input_tokens": 1000,
                    "input_tokens_details": [{"cached_tokens": 800}],
                    "output_tokens": 100,
                    "output_tokens_details": [{"reasoning_tokens": 40}],
                    "total_tokens": 1100,
                },
                "targets_info": [
                    {"original": "http://target.test"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "findings.sarif").write_text(
        json.dumps(
            {"runs": [{"results": [{"ruleId": "x"}, {"ruleId": "y"}]}]}
        ),
        encoding="utf-8",
    )

    metrics = load_run_metrics(run_dir)

    assert metrics["run_id"] == "strix_1"
    assert metrics["requests"] == 3
    assert metrics["cached_tokens"] == 800
    assert metrics["reasoning_tokens"] == 40
    assert metrics["sarif_findings"] == 2


def test_build_baseline_report_marks_interrupted_run(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "strix_2",
                "status": "running",
                "llm_usage": {"requests": 1, "total_tokens": 100},
                "targets_info": [],
            }
        ),
        encoding="utf-8",
    )

    report = build_baseline_report(
        run_dir,
        llm="deepseek/deepseek-v4-flash",
        image="ghcr.io/usestrix/strix-sandbox:1.1.0",
        mode="quick",
        budget_usd=0.10,
        completed=False,
    )

    assert report["completed"] is False
    assert report["requests"] == 1
