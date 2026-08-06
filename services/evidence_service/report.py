from __future__ import annotations

import xml.etree.ElementTree as ET

from .models import Evidence, Finding, FindingStatus


def export_json(
    findings: list[Finding],
    evidence: dict[str, Evidence],
) -> dict:
    return {
        "findings": [
            {
                "finding_id": finding.finding_id,
                "target_ref": finding.target_ref,
                "vuln_category": finding.vuln_category,
                "endpoint": finding.endpoint,
                "param": finding.param,
                "status": finding.status.value,
                "evidence_ids": finding.evidence_ids,
                "retest_proof": finding.retest_proof,
            }
            for finding in findings
        ],
        "evidence": [
            evidence[evidence_id].model_dump()
            for evidence_id in sorted(evidence)
        ],
    }


def export_markdown(
    findings: list[Finding],
    evidence: dict[str, Evidence],
) -> str:
    lines = ["# Veridix Report", ""]
    lines.append(f"Findings: {len(findings)}")
    by_category: dict[str, int] = {}
    for finding in findings:
        by_category[finding.vuln_category] = (
            by_category.get(finding.vuln_category, 0) + 1
        )
    if by_category:
        lines.append(
            "Categories: "
            + ", ".join(
                f"{category} ({count})"
                for category, count in sorted(by_category.items())
            )
        )
    lines.append("")
    for finding in findings:
        lines.append(f"## {finding.vuln_category} @ {finding.endpoint}")
        lines.append("")
        lines.append(f"- Status: {finding.status.value} ({finding.severity})")
        lines.append(f"- Target: {finding.target_ref}")
        lines.append(f"- Parameter: {finding.param or '(none)'}")
        if finding.cvss_score:
            lines.append(
                f"- CVSS: {finding.cvss_score:.1f}"
                + (f" ({finding.cvss_vector})" if finding.cvss_vector else "")
            )
        if finding.remediation:
            lines.append(f"- Remediation: {finding.remediation}")
        else:
            default_remediation = _default_remediation(finding.vuln_category)
            if default_remediation:
                lines.append(f"- Remediation: {default_remediation}")
        if finding.notes:
            lines.append(f"- Notes: {finding.notes[:400]}")
        for evidence_id in finding.evidence_ids:
            item = evidence.get(evidence_id)
            if item is None:
                lines.append(f"- Evidence {evidence_id}: (missing)")
                continue
            detail = (
                f"- Evidence {evidence_id}: "
                f"source={item.source_type} action={item.action_ref}"
            )
            if item.artifact_refs:
                detail += " artifacts=" + ", ".join(item.artifact_refs)
            if item.replay_proof:
                detail += " replay=" + (
                    "matched"
                    if item.replay_proof.get("matched")
                    else "unmatched"
                )
            lines.append(detail)
        lines.append("")
    return "\n".join(lines)


def export_html(
    findings: list[Finding],
    evidence: dict[str, Evidence],
) -> str:
    by_category: dict[str, int] = {}
    for finding in findings:
        by_category[finding.vuln_category] = (
            by_category.get(finding.vuln_category, 0) + 1
        )
    category_summary = "".join(
        f'<span class="chip">{category} ({count})</span>'
        for category, count in sorted(by_category.items())
    )

    finding_blocks: list[str] = []
    for index, finding in enumerate(findings, start=1):
        evidence_items: list[str] = []
        for evidence_id in finding.evidence_ids:
            item = evidence.get(evidence_id)
            if item is None:
                evidence_items.append(
                    f'<li class="evidence">Evidence {evidence_id}: missing</li>'
                )
                continue
            replay = (
                "matched"
                if item.replay_proof.get("matched")
                else "unmatched"
            )
            evidence_items.append(
                "<li class=\"evidence\">"
                f"<strong>{evidence_id}</strong> · {item.source_type}"
                f" · action {item.action_ref}"
                + (
                    f" · artifacts {', '.join(item.artifact_refs)}"
                    if item.artifact_refs
                    else ""
                )
                + (f" · replay {replay}" if item.replay_proof else "")
                + "</li>"
            )
        remediation = finding.remediation or _default_remediation(
            finding.vuln_category
        )
        finding_blocks.append(
            "<div class=\"finding\">"
            f"<h3>{index}. {finding.vuln_category} @ {finding.endpoint}</h3>"
            f'<p class="meta">Status {finding.status.value} · '
            f"Severity {finding.severity}"
            + (
                f" · CVSS {finding.cvss_score:.1f}"
                if finding.cvss_score
                else ""
            )
            + f" · Target {finding.target_ref}</p>"
            + (
                f"<p><strong>Parameter:</strong> {finding.param}</p>"
                if finding.param
                else ""
            )
            + (
                f"<p><strong>Remediation:</strong> {remediation}</p>"
                if remediation
                else ""
            )
            + (
                f"<p class=\"notes\"><strong>Notes:</strong> "
                f"{finding.notes[:400]}</p>"
                if finding.notes
                else ""
            )
            + ("<ul>" + "".join(evidence_items) + "</ul>")
            + "</div>"
        )

    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>Veridix Report</title><style>"
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:900px;margin:32px auto;padding:0 24px;color:#1f2937;}"
        "h1{border-bottom:2px solid #2563eb;padding-bottom:8px;}"
        ".summary{background:#f8fafc;border:1px solid #e2e8f0;"
        "border-radius:8px;padding:12px 16px;margin:16px 0;}"
        ".chip{display:inline-block;background:#e0e7ff;color:#3730a3;"
        "border-radius:999px;padding:2px 10px;margin-right:6px;"
        "font-size:12px;}.finding{border:1px solid #e2e8f0;"
        "border-radius:8px;padding:12px 16px;margin:12px 0;"
        "page-break-inside:avoid;}.meta{color:#475569;font-size:13px;}"
        ".notes{color:#64748b;font-size:13px;}"
        ".evidence{font-size:12px;color:#334155;margin:2px 0;}"
        "ul{padding-left:20px;margin:8px 0;}@media print{body{margin:0}}"
        "</style></head><body>"
        "<h1>Veridix Report</h1>"
        f"<div class=\"summary\"><strong>Findings:</strong> {len(findings)}"
        + ("<br><strong>Categories:</strong> " + category_summary)
        + "</div>"
        + "".join(finding_blocks)
        + "</body></html>"
    )


def _default_remediation(category: str) -> str:
    lookup = {
        "Exposure": (
            "Restrict the exposed service to authorized networks, require "
            "authentication, and remove or disable unused services."
        ),
        "OutdatedComponent": (
            "Upgrade the affected component to a supported version that "
            "includes the relevant security fixes."
        ),
        "SQLi": (
            "Use parameterized queries or prepared statements and validate "
            "all user-supplied input against an allowlist."
        ),
        "XSS": (
            "Encode output by context and adopt a strict Content Security "
            "Policy; sanitize user input."
        ),
        "SSRF": (
            "Validate and allowlist destination hosts/ports, block private "
            "ranges, and remove URL input from untrusted sources."
        ),
        "Authz": (
            "Enforce server-side authorization checks on every object "
            "reference and replace direct object access with capability "
            "tokens."
        ),
    }
    for key, value in lookup.items():
        if key.lower() == category.lower():
            return value
    return ""


def export_sarif(
    findings: list[Finding],
    evidence: dict[str, Evidence],
) -> dict:
    results = []
    for finding in findings:
        level = (
            "error"
            if finding.status == FindingStatus.VERIFIED
            else "warning"
            if finding.status == FindingStatus.INCONCLUSIVE
            else "note"
        )
        results.append(
            {
                "ruleId": finding.vuln_category,
                "level": level,
                "message": {
                    "text": f"{finding.vuln_category} on {finding.endpoint} ({finding.status.value})"
                },
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": finding.endpoint}
                        }
                    }
                ],
            }
        )
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "veridix", "version": "0.1.0"}},
                "results": results,
            }
        ],
    }


def export_junit(
    findings: list[Finding],
    evidence: dict[str, Evidence],
) -> str:
    suite = ET.Element(
        "testsuite",
        {
            "name": "veridix",
            "tests": str(len(findings)),
            "failures": str(
                sum(
                    finding.status
                    in (FindingStatus.VERIFIED, FindingStatus.OPEN, FindingStatus.FIXED)
                    for finding in findings
                )
            ),
        },
    )
    for finding in findings:
        case = ET.SubElement(
            suite,
            "testcase",
            {"name": finding.vuln_category, "classname": finding.endpoint},
        )
        if finding.status == FindingStatus.VERIFIED:
            failure = ET.SubElement(
                case,
                "failure",
                {"message": finding.status.value},
            )
            failure.text = (
                f"{finding.vuln_category} on {finding.endpoint}: "
                + ", ".join(finding.evidence_ids)
            )
    return ET.tostring(suite, encoding="unicode")


def export_benchmark(
    findings: list[Finding],
    evidence: dict[str, Evidence],
) -> dict:
    summary: dict[str, int] = {}
    for finding in findings:
        key = finding.status.value
        summary[key] = summary.get(key, 0) + 1
    return {
        "total_findings": len(findings),
        "summary": summary,
        "evidence_count": len(evidence),
        "findings": [
            {
                "finding_id": finding.finding_id,
                "vuln_category": finding.vuln_category,
                "endpoint": finding.endpoint,
                "status": finding.status.value,
            }
            for finding in findings
        ],
    }
