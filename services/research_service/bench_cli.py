from __future__ import annotations

import argparse
import json
import sys

from services.agent_runtime.kernel.contracts import ActionProposal, LoopSpec
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loops import (
    ScriptedLoopModel,
    VerifierOracle,
    VerifierTool,
    WebDiscoveryOracle,
    WebDiscoveryTool,
    action,
    finish,
)
from services.agent_runtime.role_benchmark import (
    compare_single_vs_multi_role,
)
from .rag_matrix_cli import DEFAULT_MODELS, run_rag_matrix
from .gate_benchmark import run_gate_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(
        description="run local benchmark suites"
    )
    parser.add_argument("--scenario", default="webappsec", help="scenario id")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--suite",
        choices=("rag", "role", "gate"),
        default="rag",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        payload = {
            "scenario_id": args.scenario,
            "runs": args.runs,
            "plan": [
                (
                    {
                        "suite": "role",
                        "mode": "single_vs_multi_role",
                        "target_ref": "https://lab.example.test",
                    }
                    if args.suite == "role"
                    else {
                        "suite": "gate",
                        "control": "http://127.0.0.1:8787",
                    }
                    if args.suite == "gate"
                    else {
                        "suite": "rag",
                        "backend": "offline",
                        "models": DEFAULT_MODELS,
                    }
                ),
            ],
            "dry_run": True,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0

    if args.suite == "role":
        report = compare_single_vs_multi_role(
            target_ref="https://lab.example.test",
            runner_factory=_role_factory(),
        )
        report["scenario_id"] = args.scenario
        report["runs"] = args.runs
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0

    if args.suite == "gate":
        report = run_gate_benchmark()
        report["scenario_id"] = args.scenario
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0 if report.get("passed") else 1

    report = run_rag_matrix(list(DEFAULT_MODELS))
    report["scenario_id"] = args.scenario
    report["runs"] = args.runs
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


def _role_factory():
    def factory(spec: LoopSpec) -> LoopRunner:
        if spec.profile == "web_discovery":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(
                            ActionProposal(
                                action_id="d1",
                                tool_ref="proxy.list",
                                input={"path": "/"},
                            )
                        ),
                        finish("coverage complete"),
                    ]
                ),
                WebDiscoveryTool(("/", "/admin", "/api/health")),
                WebDiscoveryOracle(),
            )
        if spec.profile == "verifier":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(
                            ActionProposal(
                                action_id="v1",
                                tool_ref="evidence.replay",
                                input={"candidate": "/admin"},
                            )
                        ),
                        finish("verified"),
                    ]
                ),
                VerifierTool({"/admin": "replay://proof"}),
                VerifierOracle(),
            )
        raise AssertionError(spec.profile)

    return factory


if __name__ == "__main__":
    sys.exit(main())
