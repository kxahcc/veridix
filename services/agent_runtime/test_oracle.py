from __future__ import annotations

from services.agent_runtime.oracle import FindingOracle


def test_oracle_verifies_marker_with_replay_proof() -> None:
    oracle = FindingOracle(marker="lab-secret-123")

    verdict = oracle.evaluate(
        [
            {
                "request_id": "req_1",
                "url": "http://target.test/admin",
                "request_body": "",
                "response_body": "admin api_key=lab-secret-123",
                "status_code": 200,
            }
        ]
    )

    assert verdict.decision == "verified"
    assert verdict.replay_proof["matched"] is True
    assert verdict.replay_proof["request_id"] == "req_1"


def test_oracle_inconclusive_without_marker() -> None:
    oracle = FindingOracle(marker="lab-secret-123")

    verdict = oracle.evaluate(
        [
            {
                "request_id": "req_1",
                "url": "http://target.test/",
                "response_body": "clean",
                "status_code": 200,
            }
        ]
    )

    assert verdict.decision == "inconclusive"
    assert verdict.reason == "marker_not_found"
