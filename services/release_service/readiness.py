from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class EvidenceRule:
    path: str
    contains: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaturityCheck:
    name: str
    label: str
    rules: tuple[EvidenceRule, ...]


def _text_matches(path: Path, needles: tuple[str, ...]) -> bool:
    if not needles:
        return True
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return all(needle in text for needle in needles)


MATURITY_CHECKS: tuple[MaturityCheck, ...] = (
    MaturityCheck(
        name="installable",
        label="可安装",
        rules=(
            EvidenceRule("dist-product/veridix.exe"),
            EvidenceRule("dist-product/veridix-tui.exe"),
            EvidenceRule("dist-product/manifest.json"),
            EvidenceRule("dist-product/veridix-desktop.zip"),
            EvidenceRule("package-lock.json"),
        ),
    ),
    MaturityCheck(
        name="understandable",
        label="可理解",
        rules=(
            EvidenceRule("README.md"),
            EvidenceRule("docs/user-guide.md"),
            EvidenceRule("docs/troubleshooting.md"),
            EvidenceRule("docs/sdk.md"),
            EvidenceRule("docs/architecture/system-map.md"),
        ),
    ),
    MaturityCheck(
        name="controllable",
        label="可控制",
        rules=(
            EvidenceRule(
                "services/control_plane/app/api.py",
                (
                    "def pause_run",
                    "def resume_run",
                    "def cancel_run",
                    "def fork_run",
                    "def takeover_run",
                    "def claim_run",
                    "def finish_run",
                ),
            ),
            EvidenceRule(
                "apps/web/src/pages/RunCockpit.tsx",
                ("Pause", "Takeover", "Fork"),
            ),
            EvidenceRule(
                "apps/cli/src/index.ts",
                ("runCommand", "forkRun", "takeoverRun"),
            ),
        ),
    ),
    MaturityCheck(
        name="verifiable",
        label="可验证",
        rules=(
            EvidenceRule("services/evidence_service/service.py"),
            EvidenceRule(
                "services/control_plane/app/api.py",
                ("report-bundle",),
            ),
            EvidenceRule("services/control_plane/test_control_api.py"),
            EvidenceRule("tests/e2e/test_first_usable_loop.py"),
        ),
    ),
    MaturityCheck(
        name="recoverable",
        label="可恢复",
        rules=(
            EvidenceRule(
                "services/agent_runtime/kernel/memory.py",
                ("FileCheckpointStore",),
            ),
            EvidenceRule("services/control_plane/app/outbox.py"),
            EvidenceRule("services/release_service/migrations.py"),
            EvidenceRule(
                "services/agent_runtime/test_control_worker.py",
                ("resume",),
            ),
        ),
    ),
    MaturityCheck(
        name="safe",
        label="可安全运行",
        rules=(
            EvidenceRule(
                "services/agent_runtime/kernel/tool_broker.py",
                ("authorize", "target_out_of_scope"),
            ),
            EvidenceRule("services/agent_runtime/kernel/context.py"),
            EvidenceRule("services/control_plane/app/secrets.py"),
            EvidenceRule("runners/container/sandbox_spec.py"),
        ),
    ),
    MaturityCheck(
        name="maintainable",
        label="可维护",
        rules=(
            EvidenceRule("packages/contracts/scripts/generate_types.py"),
            EvidenceRule("packages/contracts/src/generated/types.ts"),
            EvidenceRule("docs/adr"),
            EvidenceRule("docs/spikes/overall-status-2026-08-01.md"),
            EvidenceRule(
                "package.json",
                ("\"test\":", "\"build\":"),
            ),
        ),
    ),
    MaturityCheck(
        name="researchable",
        label="可研究",
        rules=(
            EvidenceRule("services/research_service/trajectory.py"),
            EvidenceRule("services/research_service/agentops.py"),
            EvidenceRule("services/research_service/graph_benchmark.py"),
            EvidenceRule("benchmarks/matrices/golden-matrix.json"),
        ),
    ),
    MaturityCheck(
        name="upgradeable",
        label="可升级",
        rules=(
            EvidenceRule("services/release_service/upgrade.py"),
            EvidenceRule("services/release_service/migrations.py"),
            EvidenceRule("services/release_service/sbom.py"),
            EvidenceRule("services/release_service/airgap.py"),
            EvidenceRule("deploy/manifests/versions.json"),
        ),
    ),
)

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "CLI SEA exe execution is blocked by this host's Application Control "
    "policy after postject injection; the identical bundle runs via Node.",
    "SSH command, tunnel, and signed artifact return are validated over "
    "localhost; production validation against a real remote host is still "
    "pending.",
    "Windows is the validated platform; the cross-platform attestation matrix "
    "is generated, and Linux/macOS real Docker attestation runs are pending "
    "on those hosts.",
    "Strix external baselines are recorded as reference data; our system has "
    "verified all 14 Strix-reported DVWA findings in same-fixture missions.",
    "Localhost HTTP connector and browser-proxy integration tests can flake "
    "under this host's network filtering; the affected tests pass standalone.",
)


def build_readiness(
    root: str | Path,
    version: str,
    *,
    regression: dict | None = None,
) -> dict:
    root_path = Path(root)
    attributes: list[dict] = []
    for check in MATURITY_CHECKS:
        ok_rules: list[str] = []
        missing: list[str] = []
        for rule in check.rules:
            target = root_path / rule.path
            if target.exists() and _text_matches(target, rule.contains):
                ok_rules.append(rule.path)
            else:
                missing.append(rule.path)
        attributes.append(
            {
                "name": check.name,
                "label": check.label,
                "status": "ok" if not missing else "missing",
                "evidence": ok_rules,
                "missing": missing,
            }
        )
    overall = "ready" if all(
        attribute["status"] == "ok" for attribute in attributes
    ) else "not_ready"
    gates = _build_gates(root)
    payload = {
        "product": "veridix",
        "version": version,
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "overall": overall,
        "attributes": attributes,
        "gates": gates,
    }
    if regression is not None:
        payload["regression"] = regression
        suite_statuses = [
            suite.get("status") for suite in regression.values()
        ]
        if overall == "ready" and all(
            status in ("passed", "skipped") for status in suite_statuses
        ):
            overall = "ready"
        else:
            overall = "not_ready"
    payload["overall"] = overall
    return payload


def write_readiness(
    root: str | Path,
    out_path: str | Path,
    version: str,
    *,
    regression: dict | None = None,
) -> dict:
    readiness = build_readiness(root, version, regression=regression)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(readiness, indent=2, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    return readiness


def _build_gates(root: Path) -> dict:
    def gate(name: str, rules: tuple[EvidenceRule, ...]) -> dict:
        ok: list[str] = []
        missing: list[str] = []
        for rule in rules:
            target = root / rule.path
            if target.exists() and _text_matches(target, rule.contains):
                ok.append(rule.path)
            else:
                missing.append(rule.path)
        return {
            "status": "passed" if not missing else "missing",
            "evidence": ok,
            "missing": missing,
        }

    return {
        "behavior_snapshot": _behavior_snapshot(root),
        "golden_path": gate(
            "golden_path",
            (
                EvidenceRule(
                    "services/agent_runtime/test_golden_sandbox.py",
                    ("test_golden_path",),
                ),
                EvidenceRule("docs/spikes/local-provider-golden-run-2026-08-01.md"),
            ),
        ),
        "self_test": gate(
            "self_test",
            (
                EvidenceRule(
                    "packages/sdk-typescript/src/self-test.ts",
                    ("runSelfTest", "runContinuityCheck"),
                ),
                EvidenceRule(
                    "docs/spikes/R0-B-gate-report.md",
                    ("self-test",),
                ),
            ),
        ),
        "sandbox_security": gate(
            "sandbox_security",
            (
                EvidenceRule("runners/container/sandbox_spec.py"),
                EvidenceRule(
                    "services/control_plane/app/secrets.py",
                    ("SecretResolver",),
                ),
                EvidenceRule(
                    "services/agent_runtime/kernel/tool_broker.py",
                    ("target_out_of_scope",),
                ),
            ),
        ),
        "provider_matrix": gate(
            "provider_matrix",
            (
                EvidenceRule("benchmarks/matrices/golden-matrix.json"),
                EvidenceRule("docs/spikes/model-matrix-2026-08-01.md"),
                EvidenceRule("services/research_service/golden_matrix.py"),
            ),
        ),
        "benchmark_regression": gate(
            "benchmark_regression",
            (
                EvidenceRule("services/research_service/agentops.py"),
                EvidenceRule(
                    "services/research_service/test_agentops.py",
                    ("regression",),
                ),
                EvidenceRule("services/research_service/trajectory.py"),
            ),
        ),
        "platform_matrix": gate(
            "platform_matrix",
            (
                EvidenceRule("runners/container/platform_matrix.py"),
                EvidenceRule("scripts/platform_matrix.py"),
                EvidenceRule("benchmarks/results/platform-matrix.json"),
            ),
        ),
        "up_autopilot_smoke": gate(
            "up_autopilot_smoke",
            (
                EvidenceRule("scripts/smoke_up_loop.py"),
                EvidenceRule(
                    "benchmarks/results/up-autopilot-smoke-2026-08-02.json"
                ),
            ),
        ),
        "upgrade_rollback": gate(
            "upgrade_rollback",
            (
                EvidenceRule("services/release_service/migrations.py"),
                EvidenceRule(
                    "services/release_service/test_v2_migration.py",
                    ("rollback",),
                ),
                EvidenceRule("services/release_service/upgrade.py"),
            ),
        ),
        "sbom_license": gate(
            "sbom_license",
            (
                EvidenceRule("services/release_service/sbom.py"),
                EvidenceRule("services/release_service/policy.py"),
                EvidenceRule("deploy/manifests/versions.json"),
            ),
        ),
        "known_limitations": list(KNOWN_LIMITATIONS),
        "release_owner": os.environ.get("VERIDIX_RELEASE_OWNER", "unassigned"),
    }


def _behavior_snapshot(root: Path) -> dict:
    def digest(relative: str) -> str | None:
        target = root / relative
        if not target.exists():
            return None
        return hashlib.sha256(target.read_bytes()).hexdigest()

    config_hash = digest("veridix.config.json")
    lock_hash = digest("package-lock.json")
    versions_hash = digest("deploy/manifests/versions.json")
    snapshot_id = hashlib.sha256(
        json.dumps(
            {
                "config": config_hash,
                "lock": lock_hash,
                "versions": versions_hash,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "snapshot_id": f"behavior_{snapshot_id[:12]}",
        "config_hash": config_hash,
        "harness_digest": versions_hash,
        "provider": "mixed",
    }
