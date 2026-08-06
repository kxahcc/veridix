from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .golden_matrix import (
    DEFAULT_MISSION,
    GoldenMatrixProvider,
    run_golden_matrix,
)
from .scenarios import load_scenario


def main() -> int:
    parser = argparse.ArgumentParser(description="run the provider golden matrix")
    parser.add_argument("--config", required=True, help="JSON matrix config")
    parser.add_argument("--out", default=None, help="optional JSON report path")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    scenario = load_scenario(config["scenario"])
    providers = [
        GoldenMatrixProvider(
            provider_id=item["provider_id"],
            model=item["model"],
            endpoint=item["endpoint"],
            api_key_ref=item.get("api_key_ref"),
            mission=item.get("mission") or DEFAULT_MISSION,
            max_turns=int(item.get("max_turns", 5)),
        )
        for item in config["providers"]
    ]
    report = run_golden_matrix(
        providers,
        scenario,
        baseline_path=config["baseline"],
        runs=int(config.get("runs", 1)),
    )
    payload = _to_dict(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(payload, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


def _to_dict(report) -> dict[str, Any]:
    return {
        "scenario_id": report.scenario_id,
        "baseline_path": report.baseline_path,
        "generated_at": report.generated_at,
        "rows": [
            {
                "provider_id": row.provider_id,
                "model": row.model,
                "runs": row.runs,
                "aggregate": row.aggregate,
                "meets_baseline": row.meets_baseline,
                "comparisons": [
                    {
                        "metric": item.metric,
                        "baseline": item.baseline,
                        "actual": item.actual,
                        "delta": item.delta,
                        "meets": item.meets,
                    }
                    for item in row.comparisons
                ],
                "harness_digest": row.harness_digest,
                "behavior_snapshot_id": row.behavior_snapshot_id,
            }
            for row in report.rows
        ],
    }


if __name__ == "__main__":
    sys.exit(main())
