from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.agent_runtime.kernel.command_tool_runner import build_command
from services.tool_pack.execution import ToolExecutionPlanner
from services.tool_pack.registry import ToolRegistry


def _web_manifest() -> dict:
    return {
        "name": "web",
        "version": "0.1.0",
        "license": "MIT",
        "capabilities": ["browser.action"],
        "runner_requirements": ["browser"],
        "network": "egress_proxy",
        "tools": ["browser.open"],
        "tool_definitions": [
            {
                "ref": "browser.open",
                "name": "browser.open",
                "schema": {"type": "object"},
                "risk_level": "L1",
                "runner": "browser",
            }
        ],
    }


def test_registry_lifecycle_records_events(tmp_path) -> None:
    path = tmp_path / "web.json"
    path.write_text(json.dumps(_web_manifest()), encoding="utf-8")
    registry = ToolRegistry()

    record = registry.load_manifest(path)
    assert record.status == "validated"
    installed = registry.install("web")
    enabled = registry.enable("web", "desktop")
    assert enabled.enabled_profiles == ("desktop",)
    registry.disable("web", "desktop")
    events = registry.pack_events("web")

    assert installed.health == "ok"
    assert [event["event"] for event in events] == [
        "discovered",
        "installed",
        "enabled",
        "disabled",
    ]


def test_registry_rejects_image_without_pinned_digest(tmp_path) -> None:
    manifest = _web_manifest()
    manifest["image"] = "example/tools:latest"
    manifest["runner_requirements"] = ["container"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="digest"):
        registry.load_manifest(path)


def test_network_recon_tools_plan_commands() -> None:
    registry = ToolRegistry()
    registry.load_manifest(Path("deploy/toolpacks/network.json"))
    planner = ToolExecutionPlanner()

    subfinder = registry.get("network.subfinder.scan")
    assert subfinder is not None
    assert planner.plan(subfinder, {"domain": "example.com"}) == [
        "subfinder",
        "-d",
        "example.com",
        "-silent",
    ]

    httpx_tool = registry.get("network.httpx.probe")
    assert httpx_tool is not None
    assert planner.plan(httpx_tool, {"target": "https://host"}) == [
        "httpx",
        "-silent",
        "-u",
        "https://host",
        "-sc",
        "-title",
        "-timeout",
        "10",
    ]

    naabu = registry.get("network.naabu.scan")
    assert naabu is not None
    assert planner.plan(naabu, {"target": "host", "ports": "1-1000"}) == [
        "naabu",
        "-host",
        "host",
        "-p",
        "1-1000",
        "-silent",
        "-sr",
        "-Pn",
    ]


def test_web_ffuf_tool_plans_command() -> None:
    registry = ToolRegistry()
    registry.load_manifest(Path("deploy/toolpacks/web.json"))
    planner = ToolExecutionPlanner()

    ffuf = registry.get("web.ffuf.scan")
    assert ffuf is not None
    assert planner.plan(
        ffuf,
        {"url": "https://target/FUZZ", "wordlist": "/tmp/words.txt"},
    ) == [
        "ffuf",
        "-w",
        "/tmp/words.txt",
        "-u",
        "https://target/FUZZ",
        "-mc",
        "200,204,301,302,307,401,403",
        "-of",
        "json",
    ]


def test_execution_planner_renders_container_command(tmp_path) -> None:
    path = tmp_path / "host.json"
    path.write_text(
        json.dumps(
            {
                "name": "host",
                "version": "0.1.0",
                "image": "veridix-tools:full",
                "digest": (
                    "sha256:d9589e6da65a6fb5d0e7a24ff8ede203747dd7c7"
                    "f8f2d0ccd40574df685b3978"
                ),
                "license": "MIT",
                "capabilities": ["port.scan"],
                "runner_requirements": ["container"],
                "tools": ["nmap.scan"],
                "tool_definitions": [
                    {
                        "ref": "nmap.scan",
                        "name": "nmap.scan",
                        "schema": {"type": "object"},
                        "risk_level": "L3",
                        "runner": "container",
                        "command_template": [
                            "nmap",
                            "-sV",
                            "-p",
                            "{ports}",
                            "{target}",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.load_manifest(path)
    definition = registry.get("nmap.scan")
    assert definition is not None

    command = ToolExecutionPlanner().plan(
        definition,
        {"target": "host", "ports": "80,443"},
    )

    assert command == ["nmap", "-sV", "-p", "80,443", "host"]


def test_command_builder_remains_available_for_native_adapters() -> None:
    assert build_command("nuclei.scan", {"target": "https://host"}) == [
        "nuclei-sandbox",
        "-u",
        "https://host",
        "-jsonl",
    ]


def _layered_registry() -> ToolRegistry:
    registry = ToolRegistry()
    pack_dir = (
        Path(__file__).resolve().parents[2]
        / "deploy"
        / "toolpacks"
    )
    for path in sorted(pack_dir.glob("*.json")):
        registry.load_manifest(path)
    return registry


def test_all_layered_packs_load_and_expose_real_tool_catalog() -> None:
    registry = _layered_registry()
    definitions = registry.list()
    refs = {definition.ref for definition in definitions}

    assert len(definitions) >= 30
    assert {
        "base.http.request",
        "base.dns.resolve",
        "nmap.scan",
        "masscan.scan",
        "web.sqlmap.scan",
        "web.directory.brute",
        "ad.ldap.search",
        "code.sast.semgrep",
        "code.secrets.detect",
        "cloud.aws.sts",
        "binary.strings",
        "nuclei.scan",
        "fscan.scan",
        "metasploit.console",
    } <= refs
    assert all(
        definition.command_template
        for definition in definitions
        if definition.runner == "container"
    )


def test_layered_tool_commands_render_with_planner() -> None:
    registry = _layered_registry()
    planner = ToolExecutionPlanner()

    assert planner.plan(
        registry.get("nuclei.scan"),
        {"target": "https://host", "templates": "cves/"},
    ) == [
        "nuclei-sandbox",
        "-u",
        "https://host",
        "-t",
        "cves/",
        "-jsonl",
    ]
    assert planner.plan(
        registry.get("fscan.scan"),
        {"target": "10.0.0.1", "ports": "80,443"},
    ) == ["fscan-sandbox", "-h", "10.0.0.1", "-p", "80,443"]
    assert planner.plan(
        registry.get("code.sast.semgrep"),
        {"path": "/workspace/input"},
    ) == [
        "semgrep",
        "scan",
        "--config",
        "/opt/veridix-rules/semgrep",
        "--project-root",
        "/workspace/input",
        "--disable-version-check",
        "--metrics=off",
        "--json",
        "/workspace/input",
    ]
    assert planner.plan(
        registry.get("base.http.request"),
        {"url": "https://host"},
    ) == [
        "curl",
        "-sS",
        "-L",
        "-X",
        "GET",
        "-w",
        "\\nHTTP_STATUS:%{http_code}",
        "https://host",
    ]
    assert planner.plan(
        registry.get("network.ping"),
        {"host": "10.0.0.1"},
    ) == ["ping", "-c", "3", "10.0.0.1"]
