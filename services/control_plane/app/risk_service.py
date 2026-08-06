from __future__ import annotations

import math
from typing import Any


CVSS_VERIDIX_METRICS = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20},
    "AC": {"L": 0.77, "H": 0.44},
    "PR": {"N": 0.85, "L": 0.62, "H": 0.27},
    "UI": {"N": 0.85, "R": 0.62},
    "C": {"H": 0.56, "L": 0.22, "N": 0.00},
    "I": {"H": 0.56, "L": 0.22, "N": 0.00},
    "A": {"H": 0.56, "L": 0.22, "N": 0.00},
}

CVSS_CATEGORY_TEMPLATES = {
    "sql": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:N",
    "command": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H",
    "rce": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H",
    "deserialization": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H",
    "ssti": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H",
    "xxe": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H",
    "xml": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H",
    "xss": "AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N",
    "cross-site": "AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N",
    "ssrf": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N",
    "lfi": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:N",
    "path": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:N",
    "traversal": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:N",
    "file": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:N",
    "read": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:N",
    "auth": "AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:N",
    "idor": "AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:N",
    "access": "AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:N",
    "privilege": "AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:N",
    "jwt": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N",
    "csrf": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N",
    "redirect": "AV:N/AC:L/PR:N/UI:R/S:U/C:N/I:L/A:N",
    "prototype": "AV:N/AC:L/PR:N/UI:R/S:C/C:H/I:H/A:H",
    "smuggling": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N",
    "websocket": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N",
    "graphql": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N",
    "race": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N",
    "exposure": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:N/A:N",
    "information": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:N/A:N",
    "disclosure": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:N/A:N",
    "outdated": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H",
    "component": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H",
    "cve": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H",
    "default": "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "credential": "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "brute": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:N/A:N",
    "session": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:N/A:N",
    "cookie": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:N/A:N",
}

SEVERITY_CVSS_TEMPLATES = {
    "critical": "AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H",
    "high": "AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H",
    "medium": "AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N",
    "low": "AV:N/AC:L/PR:L/UI:R/S:U/C:L/I:N/A:N",
    "info": "AV:N/AC:L/PR:N/UI:R/S:U/C:N/I:N/A:N",
}


def derive_cvss_vector(category: str, severity: str = "medium") -> str:
    """Pick a conservative CVSS v3.1 vector for a finding category.

    Category-specific vectors are preferred; the severity template is used
    when no known category applies so every finding gets a defensible base
    score instead of an empty placeholder.
    """
    lower = (category or "").lower()
    for marker, vector in CVSS_CATEGORY_TEMPLATES.items():
        if marker in lower:
            return f"CVSS:3.1/{vector}"
    key = (severity or "medium").lower()
    template = SEVERITY_CVSS_TEMPLATES.get(
        key,
        SEVERITY_CVSS_TEMPLATES["medium"],
    )
    return f"CVSS:3.1/{template}"


SEVERITY_WEIGHTS = {
    "critical": 9.0,
    "high": 7.0,
    "medium": 5.0,
    "low": 2.0,
    "info": 1.0,
}

STATUS_FACTORS = {
    "verified": 1.0,
    "supported": 0.8,
    "open": 0.7,
    "candidate": 0.4,
    "retest_passed": 0.8,
    "accepted_risk": 0.3,
    "reviewed": 0.5,
    "fixed": 0.1,
    "rejected": 0.0,
    "duplicate": 0.0,
    "inconclusive": 0.2,
}

OPEN_STATUSES = {
    "candidate",
    "supported",
    "verified",
    "open",
    "retest_passed",
}


def cvss_base_score(vector: str | None) -> float:
    """Compute a CVSS v3.1 base score from a vector string.

    Unknown or malformed metrics return 0.0 so the value never blocks
    findings that have not been scored yet. Scope-conditional privilege
    values and the v3.1 roundup rule are applied.
    """
    if not vector:
        return 0.0
    values: dict[str, str] = {}
    for part in str(vector).split("/"):
        if ":" in part:
            key, _, value = part.partition(":")
            values[key.upper()] = value.upper()
    try:
        av = CVSS_VERIDIX_METRICS["AV"][values["AV"]]
        ac = CVSS_VERIDIX_METRICS["AC"][values["AC"]]
        ui = CVSS_VERIDIX_METRICS["UI"][values["UI"]]
        scope = values["S"]
        c = CVSS_VERIDIX_METRICS["C"][values["C"]]
        i = CVSS_VERIDIX_METRICS["I"][values["I"]]
        a = CVSS_VERIDIX_METRICS["A"][values["A"]]
    except KeyError:
        return 0.0
    if scope == "U":
        pr = {"N": 0.85, "L": 0.62, "H": 0.27}[values["PR"]]
    elif scope == "C":
        pr = {"N": 0.85, "L": 0.68, "H": 0.50}[values["PR"]]
    else:
        return 0.0
    iss = 1.0 - (1.0 - c) * (1.0 - i) * (1.0 - a)
    exploitability = 8.22 * av * ac * pr * ui
    if scope == "U":
        impact = 6.42 * iss
        base = impact + exploitability
    else:
        impact = 7.52 * (iss - 0.029) - 3.25 * ((iss - 0.02) ** 15)
        base = 1.08 * (impact + exploitability)
    if impact <= 0:
        return 0.0
    rounded_up = math.ceil(max(base, 0.0) * 10.0) / 10.0
    return round(min(rounded_up, 10.0), 1)


def risk_score_for(severity: str, status: str) -> float:
    weight = SEVERITY_WEIGHTS.get(severity, SEVERITY_WEIGHTS["medium"])
    factor = STATUS_FACTORS.get(status, 0.5)
    return round(weight * factor, 2)


def summarize(
    findings: list[Any],
    assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    assets = assets or []
    severity_counts = {level: 0 for level in SEVERITY_WEIGHTS}
    status_counts: dict[str, int] = {}
    open_count = 0
    fixed_count = 0
    total_score = 0.0
    asset_hits: dict[str, int] = {}
    for finding in findings:
        severity = str(finding.severity or "medium")
        status = str(finding.status.value if hasattr(finding.status, "value") else finding.status)
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in OPEN_STATUSES:
            open_count += 1
        if status in ("fixed", "rejected"):
            fixed_count += 1
        total_score += risk_score_for(severity, status)
        if finding.asset_id:
            asset_hits[finding.asset_id] = asset_hits.get(finding.asset_id, 0) + 1
        else:
            for asset in assets:
                value = str(asset["value"])
                if value and (
                    value in finding.endpoint
                    or finding.endpoint.startswith(value)
                ):
                    asset_hits[asset["asset_id"]] = (
                        asset_hits.get(asset["asset_id"], 0) + 1
                    )
                    break
    top_assets = sorted(asset_hits.items(), key=lambda item: item[1], reverse=True)[:10]
    return {
        "total_findings": len(findings),
        "open_count": open_count,
        "fixed_count": fixed_count,
        "risk_score": round(total_score, 2),
        "severity_counts": severity_counts,
        "status_counts": status_counts,
        "top_assets": [
            {"asset_id": asset_id, "count": count}
            for asset_id, count in top_assets
        ],
    }
