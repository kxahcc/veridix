from __future__ import annotations

from typing import Any


CONNECTOR_PREFIXES = ("zap:", "caido:", "burp:")

_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("XSS", ("cross-site scripting", "cross site scripting", "xss")),
    ("SQLi", ("sql injection", "sqli")),
    ("CommandInjection", ("command injection", "os command")),
    ("PathTraversal", ("path traversal", "directory traversal")),
    ("SSRF", ("ssrf", "server-side request forgery")),
    ("CSRF", ("csrf", "cross-site request forgery")),
    ("Clickjacking", ("clickjacking", "anti-clickjacking")),
    (
        "InformationDisclosure",
        (
            "information disclosure",
            "server leaks",
            "leaks version",
            "banner information",
            "in page banner",
        ),
    ),
    ("MissingCSP", ("content security policy", "csp header")),
    ("CookieSecurity", ("httponly", "http-only", "cookie")),
    ("HeaderSecurity", ("header", "x-content-type-options", "x-frame-options")),
    ("OutdatedComponent", ("outdated", "obsolete", "deprecated")),
    ("DirectoryBrowsing", ("directory browsing",)),
    ("SessionManagement", ("session management",)),
    ("FuzzingActivity", ("fuzzer", "user agent fuzz")),
    (
        "AuthenticationExposure",
        ("authentication", "login page", "password", "brute force"),
    ),
)


def normalize_vuln_category(raw: str) -> str:
    lowered = raw.lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return category
    return raw or "Unknown"


def derive_connector_findings(
    observations: list[dict],
    *,
    target_ref: str,
) -> list[dict[str, Any]]:
    """Turn normalized external-scanner issues into candidate finding hints."""
    hints: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for observation in observations:
        request_id = str(observation.get("request_id") or "")
        raw_category = str(observation.get("vuln_category") or "")
        vuln_category = normalize_vuln_category(raw_category)
        if not vuln_category or not any(
            request_id.startswith(prefix)
            for prefix in CONNECTOR_PREFIXES
        ):
            continue
        endpoint = str(
            observation.get("endpoint")
            or observation.get("url")
            or target_ref
        )
        key = (vuln_category, endpoint)
        if key in seen:
            continue
        seen.add(key)
        hints.append(
            {
                "target_ref": target_ref,
                "vuln_category": vuln_category,
                "endpoint": endpoint,
                "notes": (
                    f"source={request_id} "
                    f"risk={observation.get('risk', '')} "
                    f"alert={raw_category} "
                    f"artifact={observation.get('artifact_ref', '')}"
                ),
                "evidence": {
                    "source_type": "external_scanner",
                    "artifact_refs": [
                        str(observation["artifact_ref"])
                    ]
                    if observation.get("artifact_ref")
                    else [],
                    "action_ref": request_id,
                    "confidence": 0.6,
                    "parser_version": "1",
                },
            }
        )
    return hints


def derive_finding_from_observations(
    observations: list[dict],
    *,
    target_ref: str,
    vuln_category: str,
    marker: str,
) -> dict[str, Any] | None:
    """Find the observation that proves the marker and return a finding hint."""
    for observation in observations:
        body = str(
            observation.get("response_body")
            or observation.get("body")
            or ""
        )
        url = str(observation.get("url") or observation.get("endpoint") or "")
        if marker in body or marker in url:
            return {
                "target_ref": target_ref,
                "vuln_category": vuln_category,
                "endpoint": url or target_ref,
                "notes": (
                    f"matched marker {marker} in observation "
                    f"{observation.get('request_id') or observation.get('id')}"
                ),
            }
    return None
