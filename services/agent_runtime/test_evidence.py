from __future__ import annotations

from services.agent_runtime.evidence import (
    derive_connector_findings,
    derive_finding_from_observations,
    normalize_vuln_category,
)


def test_derive_finding_from_response_body() -> None:
    observations = [
        {
            "request_id": "req_1",
            "url": "http://target.test/admin",
            "response_body": "<html>admin api_key=lab-secret-123</html>",
        }
    ]

    finding = derive_finding_from_observations(
        observations,
        target_ref="http://target.test",
        vuln_category="exposed_secret",
        marker="lab-secret-123",
    )

    assert finding is not None
    assert finding["vuln_category"] == "exposed_secret"
    assert finding["endpoint"] == "http://target.test/admin"
    assert "req_1" in finding["notes"]


def test_derive_finding_returns_none_without_marker() -> None:
    finding = derive_finding_from_observations(
        [
            {
                "request_id": "req_1",
                "url": "http://target.test/",
                "response_body": "clean",
            }
        ],
        target_ref="http://target.test",
        vuln_category="exposed_secret",
        marker="lab-secret-123",
    )

    assert finding is None


def test_derive_connector_findings_dedupes_external_issues() -> None:
    observations = [
        {
            "request_id": "caido:11",
            "endpoint": "https://target.test/?id=1",
            "vuln_category": "SQL Injection",
            "risk": "high",
            "artifact_ref": "artifact://caido/11",
        },
        {
            "request_id": "caido:12",
            "endpoint": "https://target.test/?id=1",
            "vuln_category": "SQL Injection",
            "risk": "high",
        },
        {
            "request_id": "proxy:1",
            "endpoint": "https://target.test/",
            "vuln_category": "web_issue",
        },
    ]

    hints = derive_connector_findings(
        observations,
        target_ref="https://target.test",
    )

    assert len(hints) == 1
    assert hints[0]["vuln_category"] == "SQLi"
    assert "artifact://caido/11" in hints[0]["notes"]


def test_normalize_vuln_category_maps_scanner_names() -> None:
    assert normalize_vuln_category("Cross Site Scripting (Reflected)") == "XSS"
    assert normalize_vuln_category("SQL Injection") == "SQLi"
    assert (
        normalize_vuln_category("Missing Anti-clickjacking Header")
        == "Clickjacking"
    )
    assert (
        normalize_vuln_category(
            "Server Leaks Version Information via Server "
            "HTTP Response Header Field"
        )
        == "InformationDisclosure"
    )
    assert normalize_vuln_category("Custom Scanner Name") == (
        "Custom Scanner Name"
    )
