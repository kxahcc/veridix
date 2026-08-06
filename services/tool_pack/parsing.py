from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Callable


PARSER_VERSION = "1.2"
MAX_EVIDENCE_LENGTH = 2000

_TAG_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("xss", "XSS"),
    ("sqli", "SQLi"),
    ("sql-injection", "SQLi"),
    ("lfi", "LFI"),
    ("rfi", "RFI"),
    ("ssti", "SSTI"),
    ("ssrf", "SSRF"),
    ("rce", "RCE"),
    ("cve", "CVE"),
    ("default-login", "DefaultLogin"),
    ("default-credentials", "DefaultLogin"),
    ("weak-password", "WeakCredentials"),
    ("weak-credentials", "WeakCredentials"),
    ("exposed", "Exposure"),
    ("exposure", "Exposure"),
    ("misconfig", "Misconfiguration"),
    ("misconfiguration", "Misconfiguration"),
    ("takeover", "SubdomainTakeover"),
    ("open-redirect", "OpenRedirect"),
    ("redirect", "OpenRedirect"),
    ("auth-bypass", "AuthBypass"),
    ("idor", "IDOR"),
    ("csrf", "CSRF"),
    ("cors", "CORS"),
    ("dos", "DoS"),
    ("info-disclosure", "InformationDisclosure"),
    ("information-disclosure", "InformationDisclosure"),
)

_TEXT_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("cross-site scripting", "XSS"),
    ("xss", "XSS"),
    ("sql injection", "SQLi"),
    ("sqli", "SQLi"),
    ("local file inclusion", "LFI"),
    ("remote file inclusion", "RFI"),
    ("server-side template injection", "SSTI"),
    ("template injection", "SSTI"),
    ("server-side request forgery", "SSRF"),
    ("ssrf", "SSRF"),
    ("remote code execution", "RCE"),
    ("command injection", "RCE"),
    ("code injection", "RCE"),
    ("open redirect", "OpenRedirect"),
    ("default credentials", "DefaultLogin"),
    ("information disclosure", "InformationDisclosure"),
    ("path disclosure", "InformationDisclosure"),
    ("authentication bypass", "AuthBypass"),
    ("subdomain takeover", "SubdomainTakeover"),
    ("cross-site request forgery", "CSRF"),
    ("weak credentials", "WeakCredentials"),
    ("outdated", "OutdatedComponent"),
)

_HYDRA_COLON = re.compile(
    r"^\[(\d+)\]\[([^\]]+)\]\s+(.+?):\s*(\S+)\s*:\s*(\S+)\s*$"
)
_HYDRA_NAMED = re.compile(
    r"^\[(\d+)\]\[([^\]]+)\]\s+(.+?)\s+login:\s*(\S+)\s+password:\s*(\S+)\s*$"
)
_GOBUSTER = re.compile(
    r"^/(\S+)\s+\(Status:\s*(\d+)\)\s*\[Size:\s*(\d+)\](?:\s*\[-->\s*([^\]]+)\])?"
)
_DIRB = re.compile(
    r"^\+\s+(https?://\S+)\s*\(CODE:(\d+)\|SIZE:(\d+)\)"
)
_SQLMAP_PARAM = re.compile(r"^Parameter:\s*(.+?)\s*\((\w+)\)\s*$")
_DIRSEARCH = re.compile(
    r"^\[\d{2}:\d{2}:\d{2}\]\s+(\d{3})\s+-\s+.*?-\s+(\S+)\s*$"
)
_WHATWEB = re.compile(
    r"^(https?://\S+)\s+\[(\d{3})[^\]]*\]\s+(.*)$"
)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def parse_output(
    parser_name: str,
    text: str,
) -> tuple[dict[str, Any], ...]:
    parser = PARSERS.get(parser_name, _parse_text)
    try:
        parsed = parser(text)
    except Exception:
        parsed = _parse_text(text)
    return tuple(parsed)


def _parse_text(text: str) -> list[dict[str, Any]]:
    return [{"kind": "output", "text": text[:2000]}]


def _parse_json_lines(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            observations.append({"kind": "jsonl", "record": json.loads(line)})
        except json.JSONDecodeError:
            observations.append({"kind": "line", "text": line[:2000]})
    return observations


def _truncate(value: Any, limit: int = MAX_EVIDENCE_LENGTH) -> str:
    return str(value or "")[:limit]


def _strip_ansi(value: str) -> str:
    return _ANSI.sub("", value)


def _normalize_severity(value: Any) -> str:
    return str(value or "").lower()


def _semgrep_severity(value: Any) -> str:
    lowered = str(value or "").lower()
    return {
        "error": "high",
        "warning": "medium",
        "warn": "medium",
        "info": "low",
        "note": "low",
    }.get(lowered, lowered)


def _extract_json(text: str) -> Any:
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object found in tool output")
    decoder = json.JSONDecoder()
    payload, _ = decoder.raw_decode(text[start:])
    return payload


def _category_from_tags(tags: Any) -> str:
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    for tag in tags or []:
        lowered = str(tag).lower()
        for needle, category in _TAG_CATEGORIES:
            if needle == lowered:
                return category
    return ""


def _category_from_text(value: Any) -> str:
    lowered = str(value or "").lower()
    for needle, category in _TEXT_CATEGORIES:
        if needle in lowered:
            return category
    return ""


def _cwe_values(payload: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [str(item) for item in value if item]
        if isinstance(value, str) and value:
            return [value]
    return []


def _parse_nuclei_jsonl(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            info = record.get("info") or {}
            classification = info.get("classification") or {}
            tags = info.get("tags") or []
            category = (
                _category_from_tags(tags)
                or _category_from_text(info.get("name"))
            )
            severity = _normalize_severity(info.get("severity"))
            matched_evidence = (
                record.get("curl-command")
                or record.get("extractor")
                or record.get("matcher")
                or ""
            )
            observations.append(
                {
                    "kind": "finding",
                    "parser_version": PARSER_VERSION,
                    "source": "nuclei",
                    "template_id": record.get("template-id"),
                    "matched_at": record.get("matched-at"),
                    "matcher_name": record.get("matcher-name"),
                    "info": info,
                    "severity": severity,
                    "vuln_category": category or "Exposure",
                    "tags": tags,
                    "cwe": _cwe_values(classification, "cwe-id")
                    or _cwe_values(info, "cwe"),
                    "description": _truncate(
                        info.get("description") or info.get("name") or ""
                    ),
                    "references": info.get("reference") or [],
                    "matched_evidence": _truncate(matched_evidence),
                }
            )
        except json.JSONDecodeError:
            observations.append({"kind": "line", "text": line[:2000]})
    return observations


def _parse_fscan_text(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    open_re = re.compile(
        r"^\[\+\]\s+([\w.:\[\]-]+?):(\d+)\s+(open|code:\d+)",
        re.IGNORECASE,
    )
    web_re = re.compile(
        r"^\[\+\]\s+(https?://\S+)\s+code:(\d+)\s+"
        r"len:\d+\s+title:\s*([^\s]+.*?)\s+server:\s*(\S+)",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        web_match = web_re.match(line)
        if web_match:
            url = web_match.group(1)
            code = web_match.group(2)
            title = web_match.group(3)
            server = web_match.group(4)
            observations.append(
                {
                    "kind": "service",
                    "parser_version": PARSER_VERSION,
                    "source": "fscan",
                    "endpoint": url,
                    "service": "http",
                    "product": server,
                    "version": "",
                    "state": "open",
                }
            )
            observations.append(
                {
                    "kind": "finding",
                    "parser_version": PARSER_VERSION,
                    "source": "fscan",
                    "vuln_category": "Exposure",
                    "endpoint": url,
                    "confidence": 0.7,
                    "severity": "low",
                    "evidence": (
                        f"open http service code={code} "
                        f"title={title} server={server}"
                    )[:MAX_EVIDENCE_LENGTH],
                }
            )
            continue
        open_match = open_re.match(line)
        if open_match:
            host = open_match.group(1)
            port = open_match.group(2)
            endpoint = f"{host}:{port}"
            observations.append(
                {
                    "kind": "service",
                    "parser_version": PARSER_VERSION,
                    "source": "fscan",
                    "endpoint": endpoint,
                    "service": "",
                    "product": "",
                    "version": "",
                    "state": "open",
                }
            )
            observations.append(
                {
                    "kind": "finding",
                    "parser_version": PARSER_VERSION,
                    "source": "fscan",
                    "vuln_category": "Exposure",
                    "endpoint": endpoint,
                    "confidence": 0.7,
                    "severity": "low",
                    "evidence": f"open port {port} on {host}",
                }
            )
            continue
        if line.startswith(("[", "|")):
            observations.append({"kind": "scan_line", "text": line[:2000]})
        else:
            observations.append({"kind": "line", "text": line[:2000]})
    return observations


def _parse_semgrep_json(text: str) -> list[dict[str, Any]]:
    payload = _extract_json(text)
    observations: list[dict[str, Any]] = []
    for result in payload.get("results", []):
        extra = result.get("extra") or {}
        metadata = extra.get("metadata") or {}
        start = result.get("start") or {}
        end = result.get("end") or {}
        severity = _semgrep_severity(extra.get("severity"))
        category = (
            metadata.get("category")
            or _category_from_text(metadata.get("owasp"))
            or "CodeAudit"
        )
        observations.append(
            {
                "kind": "finding",
                "parser_version": PARSER_VERSION,
                "source": "semgrep",
                "rule_id": result.get("check_id"),
                "path": result.get("path"),
                "start_line": start.get("line"),
                "end_line": end.get("line"),
                "message": extra.get("message"),
                "severity": severity,
                "vuln_category": category,
                "cwe": _cwe_values(metadata, "cwe", "cwe-id", "cwe_ids"),
                "references": metadata.get("references") or [],
                "matched_evidence": _truncate(
                    extra.get("lines") or extra.get("metavars") or ""
                ),
                "metadata": metadata,
            }
        )
    return observations


def _parse_detect_secrets_json(text: str) -> list[dict[str, Any]]:
    payload = _extract_json(text)
    observations: list[dict[str, Any]] = []
    for path, secrets in (payload.get("results") or {}).items():
        for secret in secrets or []:
            observations.append(
                {
                    "kind": "finding",
                    "parser_version": PARSER_VERSION,
                    "source": "detect-secrets",
                    "secret_type": secret.get("type"),
                    "path": path,
                    "start_line": secret.get("line_number"),
                    "severity": (
                        "high"
                        if secret.get("is_verified")
                        else "medium"
                    ),
                    "vuln_category": "HardcodedSecret",
                    "message": (
                        f"Potential {secret.get('type')} secret "
                        "in source tree"
                    ),
                    "matched_evidence": _truncate(
                        secret.get("hashed_secret") or ""
                    ),
                    "metadata": secret,
                }
            )
    return observations


def _parse_trivy_json(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    observations: list[dict[str, Any]] = []
    for result in payload.get("Results", []):
        target = result.get("Target")
        for vuln in result.get("Vulnerabilities", []):
            observations.append(
                {
                    "kind": "finding",
                    "parser_version": PARSER_VERSION,
                    "source": "trivy",
                    "target": target,
                    "vulnerability_id": vuln.get("VulnerabilityID"),
                    "severity": _normalize_severity(vuln.get("Severity")),
                    "title": vuln.get("Title"),
                    "vuln_category": "Vulnerability",
                    "cwe": _cwe_values(vuln, "CweIDs"),
                    "description": _truncate(vuln.get("Description") or ""),
                    "references": vuln.get("References") or [],
                    "installed_version": vuln.get("InstalledVersion") or "",
                    "fixed_version": vuln.get("FixedVersion") or "",
                    "published_at": vuln.get("PublishedDate") or "",
                    "pkg_name": vuln.get("PkgName") or "",
                    "matched_evidence": _truncate(
                        f"{vuln.get('PkgName') or ''} "
                        f"{vuln.get('InstalledVersion') or ''}"
                    ),
                }
            )
    return observations


def _parse_syft_json(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    artifacts = payload.get("artifacts", [])
    return [
        {
            "kind": "sbom",
            "parser_version": PARSER_VERSION,
            "source": "syft",
            "name": artifact.get("name"),
            "version": artifact.get("version"),
            "type": artifact.get("type"),
        }
        for artifact in artifacts
    ]


def _parse_grype_json(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    observations: list[dict[str, Any]] = []
    for match in payload.get("matches", []):
        vulnerability = match.get("vulnerability", {})
        artifact = match.get("artifact", {})
        fix = vulnerability.get("fix") or {}
        observations.append(
            {
                "kind": "finding",
                "parser_version": PARSER_VERSION,
                "source": "grype",
                "vulnerability_id": vulnerability.get("id"),
                "severity": _normalize_severity(
                    vulnerability.get("severity")
                ),
                "package": artifact.get("name"),
                "version": artifact.get("version"),
                "vuln_category": "Vulnerability",
                "cwe": _cwe_values(vulnerability, "cwes"),
                "description": _truncate(
                    vulnerability.get("description") or ""
                ),
                "fix_versions": fix.get("versions") or [],
                "namespace": vulnerability.get("namespace") or "",
                "matched_evidence": _truncate(
                    f"{artifact.get('name') or ''} "
                    f"{artifact.get('version') or ''}"
                ),
            }
        )
    return observations


def _parse_nmap_xml(text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(text)
    observations: list[dict[str, Any]] = []
    for host in root.iter("host"):
        addresses = host.findall(".//address")
        ip = next(
            (
                address.get("addr")
                for address in addresses
                if address.get("addrtype") == "ipv4"
            ),
            None,
        )
        hostname_el = host.find(".//hostname")
        hostname = (
            hostname_el.get("name") if hostname_el is not None else None
        )
        for port in host.iter("port"):
            state_el = port.find("state")
            if state_el is None or state_el.get("state") != "open":
                continue
            service_el = port.find("service")
            port_id = port.get("portid")
            observation = {
                "kind": "service",
                "parser_version": PARSER_VERSION,
                "source": "nmap",
                "host": hostname or ip or "",
                "ip": ip or "",
                "port": (
                    int(port_id)
                    if port_id and port_id.isdigit()
                    else port_id
                ),
                "protocol": port.get("protocol"),
                "state": state_el.get("state"),
                "service": (
                    service_el.get("name")
                    if service_el is not None
                    else ""
                ),
                "product": (
                    service_el.get("product")
                    if service_el is not None
                    else ""
                ),
                "version": (
                    service_el.get("version")
                    if service_el is not None
                    else ""
                ),
                "cpe": (
                    service_el.get("cpe")
                    if service_el is not None
                    else ""
                ),
            }
            observations.append(observation)
            observations.append(
                {
                    "kind": "finding",
                    "parser_version": PARSER_VERSION,
                    "source": "nmap",
                    "vuln_category": "Exposure",
                    "endpoint": (
                        f"{hostname or ip or ''}:{port_id}"
                    ),
                    "confidence": 0.7,
                    "severity": "low",
                    "evidence": (
                        f"open {port.get('protocol')} port {port_id} "
                        f"({observation['service']} "
                        f"{observation['product']} {observation['version']})"
                    ).strip(),
                }
            )
    return observations


def _parse_hydra_text(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        named = _HYDRA_NAMED.match(line)
        colon = _HYDRA_COLON.match(line)
        if named:
            port, service, host, username, password = named.groups()
        elif colon:
            port, service, host, username, password = colon.groups()
        else:
            observations.append({"kind": "line", "text": line[:2000]})
            continue
        observations.append(
            {
                "kind": "credential",
                "parser_version": PARSER_VERSION,
                "source": "hydra",
                "host": host.strip(),
                "port": int(port),
                "service": service,
                "username": username,
                "password": password,
                "vuln_category": "WeakCredentials",
                "severity": "high",
                "matched_evidence": _truncate(
                    f"{host.strip()}:{port} {service} "
                    f"{username}:{password}"
                ),
            }
        )
    return observations


def _parse_nikto_json(text: str) -> list[dict[str, Any]]:
    repaired = re.sub(r",\s*([}\]])", r"\1", _strip_ansi(text))
    payload = json.loads(repaired)
    hosts = payload if isinstance(payload, list) else [payload]
    observations: list[dict[str, Any]] = []
    for host in hosts:
        for vuln in host.get("vulnerabilities") or []:
            message = str(vuln.get("msg") or "")
            observations.append(
                {
                    "kind": "finding",
                    "parser_version": PARSER_VERSION,
                    "source": "nikto",
                    "host": host.get("host") or host.get("ip") or "",
                    "port": host.get("port"),
                    "vuln_category": (
                        _category_from_text(message) or "Exposure"
                    ),
                    "severity": _nikto_severity(message),
                    "rule_id": str(vuln.get("id") or vuln.get("osvdb") or ""),
                    "description": message,
                    "matched_evidence": _truncate(
                        f"{vuln.get('method') or 'GET'} "
                        f"{vuln.get('url') or ''}: {message}"
                    ),
                    "url": vuln.get("url") or "",
                    "method": vuln.get("method") or "",
                }
            )
    return observations


def _nikto_severity(message: str) -> str:
    lowered = message.lower()
    if any(
        token in lowered
        for token in (
            "xss",
            "cross-site",
            "sql",
            "remote",
            "command execution",
            "shell",
        )
    ):
        return "medium"
    if "critical" in lowered or "high" in lowered:
        return "high"
    return "low"


def _parse_wpscan_json(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    observations: list[dict[str, Any]] = []

    def add_vulnerabilities(owner: str, vulnerabilities: list[dict]) -> None:
        for vuln in vulnerabilities or []:
            title = str(vuln.get("title") or "")
            references = vuln.get("references") or {}
            observations.append(
                {
                    "kind": "finding",
                    "parser_version": PARSER_VERSION,
                    "source": "wpscan",
                    "owner": owner,
                    "rule_id": str(vuln.get("id") or ""),
                    "title": title,
                    "severity": _normalize_severity(
                        vuln.get("severity")
                        or _wpscan_severity(title)
                    ),
                    "vuln_category": (
                        _category_from_text(title) or "WordPressVuln"
                    ),
                    "cwe": _cwe_values(references, "cwe"),
                    "references": references,
                    "matched_evidence": _truncate(title),
                }
            )

    add_vulnerabilities("wordpress", payload.get("vulnerabilities"))
    for name, info in (payload.get("plugins") or {}).items():
        add_vulnerabilities(f"plugin:{name}", info.get("vulnerabilities"))
    for name, info in (payload.get("themes") or {}).items():
        add_vulnerabilities(f"theme:{name}", info.get("vulnerabilities"))
    for finding in payload.get("interesting_findings") or []:
        finding_type = str(finding.get("type") or "exposure").lower()
        title = str(finding.get("to_s") or finding.get("url") or "")
        category = (
            "Misconfiguration"
            if finding_type == "headers"
            else "InformationDisclosure"
            if finding_type
            in ("xmlrpc", "readme", "db_exports", "config_backups")
            else "Exposure"
        )
        observations.append(
            {
                "kind": "finding",
                "parser_version": PARSER_VERSION,
                "source": "wpscan",
                "owner": finding_type,
                "rule_id": f"interesting:{finding_type}",
                "title": title,
                "severity": "low",
                "vuln_category": category,
                "url": finding.get("url") or "",
                "references": finding.get("references") or {},
                "matched_evidence": _truncate(title),
            }
        )
    return observations


def _wpscan_severity(title: str) -> str:
    lowered = title.lower()
    if any(
        token in lowered
        for token in ("xss", "sql injection", "rce", "remote code execution")
    ):
        return "high"
    if "csrf" in lowered or "redirect" in lowered:
        return "medium"
    return "low"


def _parse_sqlmap_text(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        match = _SQLMAP_PARAM.match(stripped)
        if match:
            if current and current["types"]:
                observations.append(_sqlmap_finding(current))
            current = {
                "parameter": match.group(1),
                "method": match.group(2),
                "types": [],
                "payloads": [],
                "title": "",
                "vector": "",
            }
            continue
        if current is None:
            continue
        if stripped.startswith("Type:"):
            current["types"].append(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("Title:"):
            current["title"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Payload:"):
            current["payloads"].append(
                stripped.split(":", 1)[1].strip()[:MAX_EVIDENCE_LENGTH]
            )
        elif stripped.startswith("Vector:"):
            current["vector"] = stripped.split(":", 1)[1].strip()[
                :MAX_EVIDENCE_LENGTH
            ]
    if current and current["types"]:
        observations.append(_sqlmap_finding(current))
    return observations


def _sqlmap_finding(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "finding",
        "parser_version": PARSER_VERSION,
        "source": "sqlmap",
        "vuln_category": "SQLi",
        "severity": "high",
        "parameter": current["parameter"],
        "method": current["method"],
        "types": current["types"],
        "title": current["title"],
        "matched_evidence": _truncate(
            current["payloads"][0]
            if current["payloads"]
            else current["vector"] or current["title"]
        ),
        "payloads": current["payloads"],
    }


def _parse_ffuf_jsonl(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            observations.append(
                {
                    "kind": "endpoint",
                    "parser_version": PARSER_VERSION,
                    "source": "ffuf",
                    "url": record.get("url") or "",
                    "status": record.get("status"),
                    "length": record.get("length"),
                    "words": record.get("words"),
                    "lines": record.get("lines"),
                    "input": record.get("input") or {},
                }
            )
        except json.JSONDecodeError:
            observations.append({"kind": "line", "text": line[:2000]})
    return observations


def _parse_gobuster_text(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _GOBUSTER.match(line)
        if match:
            path, status, size, redirect = match.groups()
            observations.append(
                {
                    "kind": "endpoint",
                    "parser_version": PARSER_VERSION,
                    "source": "gobuster",
                    "path": "/" + path,
                    "status": int(status),
                    "size": int(size),
                    "redirect": redirect or "",
                }
            )
        else:
            observations.append({"kind": "line", "text": line[:2000]})
    return observations


def _parse_dirb_text(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _DIRB.match(line)
        if match:
            url, code, size = match.groups()
            observations.append(
                {
                    "kind": "endpoint",
                    "parser_version": PARSER_VERSION,
                    "source": "dirb",
                    "url": url,
                    "status": int(code),
                    "size": int(size),
                }
            )
        else:
            observations.append({"kind": "line", "text": line[:2000]})
    return observations


def _parse_dirsearch_text(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = _strip_ansi(line.strip())
        if not line:
            continue
        match = _DIRSEARCH.match(line)
        if match:
            status, path = match.groups()
            observations.append(
                {
                    "kind": "endpoint",
                    "parser_version": PARSER_VERSION,
                    "source": "dirsearch",
                    "path": path,
                    "status": int(status),
                }
            )
        else:
            observations.append({"kind": "line", "text": line[:2000]})
    return observations


def _parse_whatweb_text(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = _strip_ansi(line.strip())
        if not line:
            continue
        match = _WHATWEB.match(line)
        if match:
            url, status, body = match.groups()
            observations.append(
                {
                    "kind": "tech",
                    "parser_version": PARSER_VERSION,
                    "source": "whatweb",
                    "url": url,
                    "status": int(status),
                    "signals": body,
                    "technologies": _split_whatweb_signals(body),
                }
            )
        else:
            observations.append({"kind": "line", "text": line[:2000]})
    return observations


def _split_whatweb_signals(body: str) -> list[str]:
    signals: list[str] = []
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        name = part.split("[", 1)[0].strip()
        if name:
            signals.append(name)
    return signals


def _parse_subdomain_list(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        value = _strip_ansi(line.strip())
        if not value:
            continue
        observations.append(
            {
                "kind": "subdomain",
                "parser_version": PARSER_VERSION,
                "source": "subfinder",
                "value": value[:2000],
            }
        )
    return observations


def _parse_httpx_probe(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        value = _strip_ansi(line.strip())
        if not value:
            continue
        observations.append(
            {
                "kind": "http_probe",
                "parser_version": PARSER_VERSION,
                "source": "httpx",
                "value": value[:2000],
            }
        )
    return observations


def _parse_port_list(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in text.splitlines():
        value = _strip_ansi(line.strip())
        if not value:
            continue
        observations.append(
            {
                "kind": "port",
                "parser_version": PARSER_VERSION,
                "source": "naabu",
                "value": value[:2000],
            }
        )
    return observations


PARSERS: dict[str, Callable[[str], list[dict[str, Any]]]] = {
    "text": _parse_text,
    "jsonl": _parse_json_lines,
    "nuclei_jsonl": _parse_nuclei_jsonl,
    "fscan_text": _parse_fscan_text,
    "semgrep_json": _parse_semgrep_json,
    "detect_secrets_json": _parse_detect_secrets_json,
    "trivy_json": _parse_trivy_json,
    "syft_json": _parse_syft_json,
    "grype_json": _parse_grype_json,
    "nmap_xml": _parse_nmap_xml,
    "hydra_text": _parse_hydra_text,
    "nikto_json": _parse_nikto_json,
    "wpscan_json": _parse_wpscan_json,
    "sqlmap_text": _parse_sqlmap_text,
    "ffuf_jsonl": _parse_ffuf_jsonl,
    "gobuster_text": _parse_gobuster_text,
    "dirb_text": _parse_dirb_text,
    "dirsearch_text": _parse_dirsearch_text,
    "whatweb_text": _parse_whatweb_text,
    "subdomain_list": _parse_subdomain_list,
    "httpx_probe": _parse_httpx_probe,
    "port_list": _parse_port_list,
}
