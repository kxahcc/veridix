from __future__ import annotations

from services.release_service.regression import parse_pytest_summary


def test_parse_pytest_summary_counts() -> None:
    summary = parse_pytest_summary(
        "...... [100%]\n6 passed, 1 failed, 2 skipped in 1.2s"
    )

    assert summary == {
        "status": "failed",
        "passed": 6,
        "failed": 1,
        "errors": 0,
        "skipped": 2,
        "detail": "6 passed, 1 failed, 0 error, 2 skipped",
    }


def test_parse_pytest_summary_without_failures() -> None:
    summary = parse_pytest_summary("52 passed, 1 warning in 30s")

    assert summary["status"] == "passed"
    assert summary["passed"] == 52
    assert summary["failed"] == 0
    assert summary["errors"] == 0
    assert summary["skipped"] == 0


def test_parse_pytest_summary_missing() -> None:
    summary = parse_pytest_summary("no pytest summary")

    assert summary["status"] == "unknown"


def test_parse_pytest_summary_counts_errors() -> None:
    summary = parse_pytest_summary("100 passed, 1 error in 2.0s")

    assert summary["status"] == "failed"
    assert summary["errors"] == 1


def test_parse_pytest_summary_with_leading_failures() -> None:
    summary = parse_pytest_summary("2 failed, 102 passed in 113.1s")

    assert summary["status"] == "failed"
    assert summary["failed"] == 2
    assert summary["passed"] == 102
