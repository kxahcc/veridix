from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_run_metrics(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir) / "run.json"
    data = json.loads(run_path.read_text(encoding="utf-8"))
    usage = data.get("llm_usage") or {}
    cached_tokens = 0
    for detail in usage.get("input_tokens_details") or []:
        cached_tokens += int(detail.get("cached_tokens", 0) or 0)
    reasoning_tokens = 0
    for detail in usage.get("output_tokens_details") or []:
        reasoning_tokens += int(detail.get("reasoning_tokens", 0) or 0)
    return {
        "run_id": data.get("run_id"),
        "status": data.get("status"),
        "requests": int(usage.get("requests", 0) or 0),
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
        "targets": [
            target.get("original")
            for target in data.get("targets_info") or []
        ],
        "sarif_findings": count_sarif_findings(run_dir),
    }


def count_sarif_findings(run_dir: str | Path) -> int:
    sarif_path = Path(run_dir) / "findings.sarif"
    if not sarif_path.exists():
        return 0
    data = json.loads(sarif_path.read_text(encoding="utf-8"))
    results = []
    for run in data.get("runs") or []:
        results.extend(run.get("results") or [])
    return len(results)


def build_baseline_report(
    run_dir: str | Path,
    *,
    llm: str,
    image: str,
    mode: str,
    budget_usd: float,
    completed: bool,
) -> dict[str, Any]:
    return {
        "product": "strix-external-baseline",
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "llm": llm,
        "image": image,
        "mode": mode,
        "budget_usd": budget_usd,
        "completed": completed,
        **load_run_metrics(run_dir),
    }
