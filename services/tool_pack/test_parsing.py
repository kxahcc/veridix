from __future__ import annotations

import json

from services.agent_runtime.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
)
from services.tool_pack.execution import ContainerToolRunner
from services.tool_pack.execution import validate_tool_arguments
from services.tool_pack.models import ToolDefinition, ToolPackManifest
from services.tool_pack.parsing import parse_output


def test_nuclei_jsonl_becomes_findings() -> None:
    records = parse_output(
        "nuclei_jsonl",
        json.dumps(
            {
                "template-id": "cve-2020-1234",
                "matched-at": "https://host/admin",
                "matcher-name": "http-response",
                "info": {"severity": "high"},
            }
        ),
    )

    assert records[0]["kind"] == "finding"
    assert records[0]["template_id"] == "cve-2020-1234"
    assert records[0]["severity"] == "high"


def test_recon_list_parsers_emit_structured_observations() -> None:
    subdomains = parse_output("subdomain_list", "api.example.com\nadmin.example.com")
    probes = parse_output("httpx_probe", "https://host [200] [title]")
    ports = parse_output("port_list", "80\n443")

    assert [item["value"] for item in subdomains] == [
        "api.example.com",
        "admin.example.com",
    ]
    assert probes[0]["kind"] == "http_probe"
    assert [item["value"] for item in ports] == ["80", "443"]


def test_semgrep_and_dependency_parsers_return_findings() -> None:
    semgrep = parse_output(
        "semgrep_json",
        json.dumps(
            {
                "results": [
                    {
                        "check_id": "python.lang.security.eval",
                        "path": "app.py",
                        "start": {"line": 12},
                        "extra": {"message": "eval", "severity": "ERROR"},
                    }
                ]
            }
        ),
    )
    trivy = parse_output(
        "trivy_json",
        json.dumps(
            {
                "Results": [
                    {
                        "Target": "app",
                        "Vulnerabilities": [
                            {
                                "VulnerabilityID": "CVE-2026-0001",
                                "Severity": "HIGH",
                                "Title": "demo",
                            }
                        ],
                    }
                ]
            }
        ),
    )

    assert semgrep[0]["rule_id"] == "python.lang.security.eval"
    assert semgrep[0]["start_line"] == 12
    assert trivy[0]["vulnerability_id"] == "CVE-2026-0001"


def test_semgrep_parser_extracts_json_when_scan_status_precedes() -> None:
    payload = json.dumps(
        {
            "results": [
                {
                    "check_id": "python.lang.security.eval",
                    "path": "/workspace/input/vuln.py",
                    "start": {"line": 3},
                    "end": {"line": 3},
                    "extra": {
                        "message": "eval detected",
                        "severity": "WARNING",
                        "metadata": {
                            "category": "security",
                            "cwe": ["CWE-95"],
                        },
                    },
                }
            ],
            "errors": [],
        }
    )
    noisy = "Scan Status\n" + payload + "\nScan completed\n"

    findings = parse_output("semgrep_json", noisy)

    assert len(findings) == 1
    assert findings[0]["rule_id"] == "python.lang.security.eval"


def test_detect_secrets_parser_returns_structured_findings() -> None:
    payload = json.dumps(
        {
            "version": "1.5.0",
            "results": {
                "/workspace/input/config.py": [
                    {
                        "type": "AWS Key",
                        "line_number": 4,
                        "is_verified": True,
                        "hashed_secret": "sha256:abc",
                    }
                ]
            },
        }
    )

    findings = parse_output("detect_secrets_json", payload)

    assert len(findings) == 1
    assert findings[0]["source"] == "detect-secrets"
    assert findings[0]["severity"] == "high"
    assert findings[0]["start_line"] == 4
    assert findings[0]["vuln_category"] == "HardcodedSecret"


def test_malformed_json_falls_back_to_text_observation() -> None:
    records = parse_output("nuclei_jsonl", "not json\n")

    assert records == ({"kind": "line", "text": "not json"},)


def test_validate_tool_arguments_reports_missing_and_type_errors() -> None:
    definition = ToolDefinition(
        ref="test.tool",
        name="test.tool",
        schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "ports": {"type": "integer"},
            },
            "required": ["url", "ports"],
        },
    )

    errors = validate_tool_arguments(
        definition,
        {"url": "https://host", "ports": "80"},
    )

    assert errors == [
        "argument ports must be numeric",
    ]
    assert validate_tool_arguments(definition, {"url": "", "ports": 80}) == [
        "missing required argument url",
    ]


def test_fscan_text_lines_are_preserved() -> None:
    records = parse_output("fscan_text", "[*] 10.0.0.1:80 open\n")

    assert records[0]["kind"] == "scan_line"


def test_nuclei_jsonl_carries_rich_evidence_metadata() -> None:
    records = parse_output(
        "nuclei_jsonl",
        json.dumps(
            {
                "template-id": "cve-2020-1234",
                "matched-at": "https://host/admin",
                "matcher-name": "http-response",
                "info": {
                    "severity": "high",
                    "name": "Cross-Site Scripting (XSS)",
                    "tags": ["xss", "cve"],
                    "classification": {"cwe-id": ["CWE-79"]},
                    "description": "Reflected XSS",
                },
                "curl-command": "curl 'https://host/admin?q=1'",
            }
        ),
    )

    assert records[0]["kind"] == "finding"
    assert records[0]["vuln_category"] == "XSS"
    assert records[0]["cwe"] == ["CWE-79"]
    assert records[0]["severity"] == "high"
    assert "curl" in records[0]["matched_evidence"]
    assert records[0]["parser_version"] == "1.2"


def test_semgrep_json_maps_severity_and_metadata() -> None:
    records = parse_output(
        "semgrep_json",
        json.dumps(
            {
                "results": [
                    {
                        "check_id": "python.lang.security.eval",
                        "path": "app.py",
                        "start": {"line": 12},
                        "end": {"line": 12},
                        "extra": {
                            "message": "eval",
                            "severity": "ERROR",
                            "lines": "eval(user_input)",
                            "metadata": {
                                "cwe": ["CWE-95"],
                                "category": "security",
                            },
                        },
                    }
                ]
            }
        ),
    )

    assert records[0]["severity"] == "high"
    assert records[0]["cwe"] == ["CWE-95"]
    assert records[0]["matched_evidence"] == "eval(user_input)"


def test_trivy_json_carries_cwe_and_fix_information() -> None:
    records = parse_output(
        "trivy_json",
        json.dumps(
            {
                "Results": [
                    {
                        "Target": "app",
                        "Vulnerabilities": [
                            {
                                "VulnerabilityID": "CVE-2026-0001",
                                "Severity": "HIGH",
                                "Title": "demo",
                                "CweIDs": ["CWE-79"],
                                "InstalledVersion": "1.0",
                                "FixedVersion": "1.1",
                            }
                        ],
                    }
                ]
            }
        ),
    )

    assert records[0]["vuln_category"] == "Vulnerability"
    assert records[0]["cwe"] == ["CWE-79"]
    assert records[0]["severity"] == "high"
    assert records[0]["fixed_version"] == "1.1"


def test_nmap_xml_becomes_service_observations() -> None:
    records = parse_output(
        "nmap_xml",
        (
            "<nmaprun>"
            "<host><address addr='10.0.0.5' addrtype='ipv4'/>"
            "<hostnames><hostname name='db.internal'/></hostnames>"
            "<ports><port protocol='tcp' portid='3306'>"
            "<state state='open'/>"
            "<service name='mysql' product='MySQL' version='8.0'/>"
            "</port></ports></host></nmaprun>"
        ),
    )

    assert records[0]["kind"] == "service"
    assert records[0]["host"] == "db.internal"
    assert records[0]["port"] == 3306
    assert records[0]["service"] == "mysql"
    assert records[0]["version"] == "8.0"
    finding = next(
        record for record in records if record["kind"] == "finding"
    )
    assert finding["vuln_category"] == "Exposure"
    assert finding["endpoint"] == "db.internal:3306"
    assert "mysql" in finding["evidence"]


def test_hydra_text_becomes_credential_observations() -> None:
    records = parse_output(
        "hydra_text",
        (
            "[22][ssh] 10.0.0.5: root : toor\n"
            "[80][http-get] 10.0.0.6 login: admin password: secret\n"
        ),
    )

    assert records[0]["kind"] == "credential"
    assert records[0]["service"] == "ssh"
    assert records[0]["username"] == "root"
    assert records[0]["password"] == "toor"
    assert records[1]["username"] == "admin"
    assert records[1]["password"] == "secret"


def test_nikto_json_becomes_findings() -> None:
    records = parse_output(
        "nikto_json",
        (
            '{"host":"target","ip":"10.0.0.5","port":80,'
            '"vulnerabilities":[{"id":"999999",'
            '"msg":"Cross-Site Scripting in query parameter",'
            '"url":"/","method":"GET"},]}'
        ),
    )

    assert records[0]["kind"] == "finding"
    assert records[0]["vuln_category"] == "XSS"
    assert "Cross-Site" in records[0]["matched_evidence"]


def test_fscan_text_becomes_exposure_findings() -> None:
    records = parse_output(
        "fscan_text",
        (
            "[+] 10.0.0.5:22 open\n"
            "[+] http://compose-dvwa-1          code:302 len:0     "
            "title:Login :: DVWA server:Apache/2.4.25 [dvwa]\n"
            "[*] 扫描完成\n"
        ),
    )

    services = [
        record for record in records if record["kind"] == "service"
    ]
    findings = [
        record for record in records if record["kind"] == "finding"
    ]
    assert len(services) == 2
    assert len(findings) == 2
    assert findings[0]["vuln_category"] == "Exposure"
    assert findings[0]["endpoint"] == "10.0.0.5:22"
    assert findings[1]["endpoint"] == "http://compose-dvwa-1"
    assert "Apache" in findings[1]["evidence"]


def test_wpscan_json_becomes_findings() -> None:
    records = parse_output(
        "wpscan_json",
        json.dumps(
            {
                "version": "6.4",
                "vulnerabilities": [
                    {
                        "id": "WP-123",
                        "title": "WordPress Plugin XSS",
                        "references": {"cwe": ["CWE-79"]},
                    }
                ],
            }
        ),
    )

    assert records[0]["kind"] == "finding"
    assert records[0]["owner"] == "wordpress"
    assert records[0]["vuln_category"] == "XSS"
    assert records[0]["cwe"] == ["CWE-79"]


def test_wpscan_interesting_findings_become_findings() -> None:
    records = parse_output(
        "wpscan_json",
        json.dumps(
            {
                "interesting_findings": [
                    {
                        "type": "xmlrpc",
                        "url": "http://target/xmlrpc.php",
                        "to_s": "XML-RPC seems to be enabled",
                    },
                    {
                        "type": "headers",
                        "url": "http://target/",
                        "to_s": "Server: Apache",
                    },
                ]
            }
        ),
    )

    assert [record["vuln_category"] for record in records] == [
        "InformationDisclosure",
        "Misconfiguration",
    ]
    assert records[0]["rule_id"] == "interesting:xmlrpc"
    assert records[1]["owner"] == "headers"


def test_sqlmap_text_becomes_injection_findings() -> None:
    records = parse_output(
        "sqlmap_text",
        (
            "[INFO] testing connection to the target URL\n"
            "Parameter: id (GET)\n"
            "    Type: boolean-based blind\n"
            "    Title: AND boolean-based blind\n"
            "    Payload: id=1 AND 1=1\n"
            "Parameter: user (POST)\n"
            "    Type: time-based blind\n"
            "    Payload: user=x' AND SLEEP(5)-- -\n"
        ),
    )

    assert [record["parameter"] for record in records] == ["id", "user"]
    assert records[0]["vuln_category"] == "SQLi"
    assert records[0]["payloads"] == ["id=1 AND 1=1"]


def test_fuzzer_parsers_emit_endpoint_observations() -> None:
    ffuf = parse_output(
        "ffuf_jsonl",
        json.dumps(
            {
                "input": {"FUZZ": "admin"},
                "status": 200,
                "length": 1234,
                "url": "https://host/admin",
            }
        ),
    )
    gobuster = parse_output(
        "gobuster_text",
        "/admin (Status: 200) [Size: 1234]\n",
    )
    dirb = parse_output(
        "dirb_text",
        "+ http://host/admin (CODE:200|SIZE:1234)\n",
    )

    assert ffuf[0]["kind"] == "endpoint"
    assert ffuf[0]["status"] == 200
    assert gobuster[0]["path"] == "/admin"
    assert gobuster[0]["size"] == 1234
    assert dirb[0]["url"] == "http://host/admin"


def test_dirsearch_and_whatweb_parsers_emit_observations() -> None:
    dirsearch = parse_output(
        "dirsearch_text",
        "[12:34:56] 200 -  123B - /admin\n",
    )
    whatweb = parse_output(
        "whatweb_text",
        (
            "http://10.0.0.1 [200 OK] "
            "Apache[2.4.41], Title[Admin], HTTPServer[Debian]\n"
        ),
    )

    assert dirsearch[0]["kind"] == "endpoint"
    assert dirsearch[0]["path"] == "/admin"
    assert dirsearch[0]["status"] == 200
    assert whatweb[0]["kind"] == "tech"
    assert whatweb[0]["status"] == 200
    assert "Apache" in whatweb[0]["technologies"]


def test_container_runner_carries_parsed_observations() -> None:
    class FakeBackend:
        def start(self, spec):
            return "handle"

        def exec(self, handle, request: ExecutionRequest) -> ExecutionResult:
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout=json.dumps(
                    {
                        "template-id": "cve-2026-0001",
                        "matched-at": "https://host",
                        "info": {"severity": "high"},
                    }
                ),
            )

        def destroy(self, handle) -> None:
            return None

    manifest = ToolPackManifest(
        name="vulnscan",
        version="0.1.0",
        image="veridix-tools:full",
        digest="sha256:" + "a" * 64,
    )
    definition = ToolDefinition(
        ref="nuclei.scan",
        name="nuclei.scan",
        output_parser="nuclei_jsonl",
        command_template=["nuclei", "-u", "{target}"],
    )
    runner = ContainerToolRunner(
        manifest=manifest,
        definition=definition,
        backend_factory=lambda: FakeBackend(),
    )

    result = runner.execute(
        ExecutionRequest(
            action_id="action_1",
            run_id="run_1",
            tool_ref="nuclei.scan",
            input={"target": "https://host"},
            idempotency_key="run_1:nuclei.scan:1",
        )
    )

    assert result.observations[0]["template_id"] == "cve-2026-0001"


def test_container_runner_honors_docker_network_override() -> None:
    class RecordingBackend:
        def __init__(self) -> None:
            self.spec = None

        def start(self, spec):
            self.spec = spec
            return "handle"

        def exec(self, handle, request: ExecutionRequest) -> ExecutionResult:
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout="",
            )

        def destroy(self, handle) -> None:
            return None

    manifest = ToolPackManifest(
        name="web",
        version="0.1.0",
        image="veridix-tools:full",
        digest="sha256:" + "a" * 64,
        network="egress_proxy",
    )
    definition = ToolDefinition(
        ref="web.nikto.scan",
        name="web.nikto.scan",
        output_parser="text",
        command_template=["nikto", "-h", "{url}"],
    )
    backend = RecordingBackend()
    runner = ContainerToolRunner(
        manifest=manifest,
        definition=definition,
        backend_factory=lambda: backend,
        network="compose_dvwa-net",
    )

    runner.execute(
        ExecutionRequest(
            action_id="action_network",
            run_id="run_network",
            tool_ref="web.nikto.scan",
            input={"url": "http://compose-dvwa-1"},
            idempotency_key="run_network:web.nikto.scan:1",
        )
    )

    assert backend.spec.network.mode == "egress_proxy"
    assert backend.spec.network.docker_network == "compose_dvwa-net"


def test_container_runner_passes_mounts_to_sandbox() -> None:
    class RecordingBackend:
        def __init__(self) -> None:
            self.spec = None

        def start(self, spec):
            self.spec = spec
            return "handle"

        def exec(self, handle, request: ExecutionRequest) -> ExecutionResult:
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout="",
            )

        def destroy(self, handle) -> None:
            return None

    manifest = ToolPackManifest(
        name="vulnscan",
        version="0.1.0",
        image="veridix-tools:full",
        digest="sha256:" + "b" * 64,
        network="egress_proxy",
    )
    definition = ToolDefinition(
        ref="nuclei.scan",
        name="nuclei.scan",
        output_parser="nuclei_jsonl",
        command_template=["nuclei", "-u", "{url}", "-t", "{templates}"],
    )
    backend = RecordingBackend()
    runner = ContainerToolRunner(
        manifest=manifest,
        definition=definition,
        backend_factory=lambda: backend,
        mounts=(
            {
                "source": "/host/templates",
                "target": "/root/nuclei-templates",
                "mode": "ro",
            },
        ),
    )

    runner.execute(
        ExecutionRequest(
            action_id="action_nuclei",
            run_id="run_nuclei",
            tool_ref="nuclei.scan",
            input={
                "url": "http://compose-dvwa-1",
                "templates": "/root/nuclei-templates/http",
            },
            idempotency_key="run_nuclei:1",
        )
    )

    assert backend.spec.filesystem.mounts == (
        {
            "source": "/host/templates",
            "target": "/root/nuclei-templates",
            "mode": "ro",
        },
    )


def test_container_runner_sets_workspace_cwd() -> None:
    class RecordingBackend:
        def __init__(self) -> None:
            self.request = None

        def start(self, spec):
            return "handle"

        def exec(self, handle, request: ExecutionRequest) -> ExecutionResult:
            self.request = request
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                stdout="",
            )

        def destroy(self, handle) -> None:
            return None

    manifest = ToolPackManifest(
        name="code",
        version="0.1.0",
        image="veridix-tools:code-lite",
        digest="sha256:" + "c" * 64,
        network="none",
    )
    definition = ToolDefinition(
        ref="code.secrets.detect",
        name="code.secrets.detect",
        output_parser="detect_secrets_json",
        command_template=["detect-secrets", "scan", "--all-files", "{path}"],
    )
    backend = RecordingBackend()
    runner = ContainerToolRunner(
        manifest=manifest,
        definition=definition,
        backend_factory=lambda: backend,
    )

    runner.execute(
        ExecutionRequest(
            action_id="action_code",
            run_id="run_code",
            tool_ref="code.secrets.detect",
            input={"path": "/workspace/input"},
            idempotency_key="run_code:code.secrets.detect:1",
        )
    )

    assert backend.request.input["command"] == [
        "detect-secrets",
        "scan",
        "--all-files",
        "/workspace/input",
    ]
    assert backend.request.input["cwd"] == "/workspace/input"
