from __future__ import annotations

import argparse
import json
import sys

from services.agent_runtime.golden import GoldenRunDriver, GoldenRunSpec


def main() -> int:
    parser = argparse.ArgumentParser(description="run the Reference Golden fixture")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mission", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--behavior", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-ref", default=None)
    parser.add_argument("--max-turns", type=int, default=5)
    parser.add_argument(
        "--thinking-mode",
        choices=["enabled", "disabled"],
        default=None,
    )
    parser.add_argument(
        "--tool-choice",
        choices=["auto", "none", "required"],
        default=None,
    )
    parser.add_argument("--streaming", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "ready": True,
                    "run_id": args.run_id,
                    "mission": args.mission,
                    "target": args.target,
                    "behavior": args.behavior,
                    "endpoint": args.endpoint,
                    "model": args.model,
                    "api_key_ref": args.api_key_ref,
                    "max_turns": args.max_turns,
                    "thinking_mode": args.thinking_mode,
                    "tool_choice": args.tool_choice,
                    "streaming": args.streaming,
                },
                indent=2,
            )
        )
        return 0

    spec = GoldenRunSpec(
        run_id=args.run_id,
        mission=args.mission,
        target_ref=args.target,
        behavior_snapshot=args.behavior,
        provider_endpoint=args.endpoint,
        provider_model=args.model,
        api_key_ref=args.api_key_ref,
        max_turns=args.max_turns,
        thinking_mode=args.thinking_mode,
        tool_choice=args.tool_choice,
        streaming=args.streaming,
    )
    result = GoldenRunDriver().run(spec)
    payload = {
        "run_id": result.run_id,
        "status": result.status,
        "metrics": result.metrics,
        "evidence_refs": list(result.evidence_refs),
        "oracle_passed": result.oracle_passed,
        "harness_digest": result.harness_digest,
        "behavior_snapshot_id": result.behavior_snapshot_id,
        "error": result.error,
        "finding": (
            {
                "finding_id": result.finding.finding_id,
                "status": result.finding.status.value,
            }
            if result.finding is not None
            else None
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0 if result.oracle_passed else 1


if __name__ == "__main__":
    sys.exit(main())
