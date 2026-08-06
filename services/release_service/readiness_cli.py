from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.release_service.readiness import write_readiness


def main() -> int:
    parser = argparse.ArgumentParser(
        description="generate release readiness evidence",
    )
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument(
        "--out",
        default="dist-product/release-readiness.json",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="run python/npm/e2e regressions and record real results",
    )
    parser.add_argument(
        "--include-web-faults",
        action="store_true",
        help="also run the Web fault-drill E2E suite",
    )
    parser.add_argument(
        "--include-e2e",
        action="store_true",
        help="also run the first-usable-loop E2E suite",
    )
    parser.add_argument(
        "--broad",
        action="store_true",
        help="run the broad control-plane/agent-runtime directory suites",
    )
    args = parser.parse_args()

    regression = None
    if args.run_tests:
        from services.release_service.regression import run_regressions

        regression = run_regressions(
            ROOT,
            sys.executable,
            include_web_faults=args.include_web_faults,
            include_e2e=args.include_e2e,
            broad=args.broad,
        )
    readiness = write_readiness(
        ROOT,
        args.out,
        args.version,
        regression=regression,
    )
    print(json.dumps(readiness, ensure_ascii=True, indent=2))
    return 0 if readiness["overall"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
