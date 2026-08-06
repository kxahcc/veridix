from __future__ import annotations

import json

from services.release_service.readiness import (
    MATURITY_CHECKS,
    build_readiness,
    write_readiness,
)


def test_maturity_checks_cover_nine_attributes() -> None:
    names = [check.name for check in MATURITY_CHECKS]

    assert len(names) == 9
    assert names == [
        "installable",
        "understandable",
        "controllable",
        "verifiable",
        "recoverable",
        "safe",
        "maintainable",
        "researchable",
        "upgradeable",
    ]


def test_build_readiness_reports_ok_when_evidence_exists(tmp_path) -> None:
    installable = next(
        check for check in MATURITY_CHECKS if check.name == "installable"
    )
    for rule in installable.rules:
        target = tmp_path / rule.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder", encoding="utf-8")

    readiness = build_readiness(tmp_path, "0.1.0")
    attribute = next(
        item
        for item in readiness["attributes"]
        if item["name"] == "installable"
    )

    assert attribute["status"] == "ok"
    assert len(attribute["evidence"]) == len(installable.rules)
    assert attribute["missing"] == []


def test_build_readiness_reports_missing_evidence(tmp_path) -> None:
    readiness = build_readiness(tmp_path, "0.1.0")

    assert readiness["overall"] == "not_ready"
    assert any(attribute["missing"] for attribute in readiness["attributes"])


def test_write_readiness_round_trip(tmp_path) -> None:
    out = tmp_path / "release-readiness.json"

    readiness = write_readiness(tmp_path, out, "0.1.0")
    loaded = json.loads(out.read_text(encoding="utf-8"))

    assert loaded["product"] == "veridix"
    assert loaded["version"] == "0.1.0"
    assert len(loaded["attributes"]) == len(readiness["attributes"])


def test_build_readiness_includes_release_gates(tmp_path) -> None:
    readiness = build_readiness(tmp_path, "0.1.0")

    gates = readiness["gates"]
    for key in (
        "behavior_snapshot",
        "golden_path",
        "self_test",
        "sandbox_security",
        "provider_matrix",
        "benchmark_regression",
        "upgrade_rollback",
        "sbom_license",
        "known_limitations",
        "release_owner",
    ):
        assert key in gates
    assert gates["behavior_snapshot"]["snapshot_id"].startswith("behavior_")
    assert isinstance(gates["known_limitations"], list)
    assert gates["release_owner"] == "unassigned"


def test_build_readiness_records_regression_and_marks_not_ready(
    tmp_path,
) -> None:
    regression = {
        "python": {
            "status": "failed",
            "passed": 10,
            "failed": 1,
            "skipped": 0,
            "detail": "10 passed, 1 failed",
        },
        "typescript": {"status": "passed", "exit_code": 0},
        "e2e": {"status": "passed", "passed": 1, "failed": 0, "skipped": 0},
    }

    readiness = build_readiness(
        tmp_path,
        "0.1.0",
        regression=regression,
    )

    assert readiness["regression"] == regression
    assert readiness["overall"] == "not_ready"
