from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from services.lab_provider.app.main import app


def test_models_and_chat_completion_flow() -> None:
    with TestClient(app) as client:
        models = client.get("/models")
        assert models.status_code == 200
        assert [item["id"] for item in models.json()["data"]] == [
            "veridix-lab-flash",
            "veridix-lab-pro",
        ]
        assert client.get("/v1/models").status_code == 200

        first = client.post(
            "/v1/chat/completions",
            json={
                "model": "veridix-lab-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": "Target in scope: https://lab.example.test\nMission: probe once",
                    }
                ],
            },
        )
        assert first.status_code == 200
        first_message = first.json()["choices"][0]["message"]
        assert first_message["tool_calls"][0]["function"]["name"] == "shell_probe"
        arguments = json.loads(first_message["tool_calls"][0]["function"]["arguments"])
        assert arguments["target"] == "https://lab.example.test"

        second = client.post(
            "/v1/chat/completions",
            json={
                "model": "veridix-lab-flash",
                "messages": [
                    {"role": "user", "content": "probe once"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": first_message["tool_calls"],
                    },
                    {"role": "tool", "tool_call_id": "call_lab_probe", "content": "ok"},
                ],
            },
        )
        assert second.status_code == 200
        assert second.json()["choices"][0]["message"]["content"] == "probe complete"


def test_optional_api_key_is_enforced(monkeypatch) -> None:
    monkeypatch.setenv("LAB_PROVIDER_API_KEY", "lab-key")
    with TestClient(app) as client:
        unauthorized = client.post(
            "/v1/chat/completions",
            json={
                "model": "veridix-lab-flash",
                "messages": [{"role": "user", "content": "probe"}],
            },
        )
        assert unauthorized.status_code == 401

        authorized = client.post(
            "/v1/chat/completions",
            json={
                "model": "veridix-lab-flash",
                "messages": [{"role": "user", "content": "probe"}],
            },
            headers={"Authorization": "Bearer lab-key"},
        )
        assert authorized.status_code == 200


@pytest.mark.parametrize(
    "scenario,expected_tool",
    [
        ("nikto", "web_nikto_scan"),
        ("sqlmap", "web_sqlmap_scan"),
    ],
)
def test_scanner_scenarios_emit_pack_tools(
    monkeypatch,
    scenario: str,
    expected_tool: str,
) -> None:
    monkeypatch.setenv("LAB_PROVIDER_SCENARIO", scenario)
    with TestClient(app) as client:
        first = client.post(
            "/v1/chat/completions",
            json={
                "model": "veridix-lab-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Target in scope: http://compose-dvwa-1:80\n"
                            "Mission: scan once"
                        ),
                    }
                ],
            },
        )
        first_message = first.json()["choices"][0]["message"]
        assert first_message["tool_calls"][0]["function"]["name"] == (
            expected_tool
        )
        arguments = json.loads(
            first_message["tool_calls"][0]["function"]["arguments"]
        )
        assert arguments["url"] == "http://compose-dvwa-1:80"

        second = client.post(
            "/v1/chat/completions",
            json={
                "model": "veridix-lab-flash",
                "messages": [
                    {"role": "user", "content": "scan once"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": first_message["tool_calls"],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_lab_probe",
                        "content": "ok",
                    },
                ],
            },
        )
        assert second.status_code == 200
        assert (
            second.json()["choices"][0]["message"]["content"]
            == "scan complete"
        )


@pytest.mark.parametrize(
    "scenario,expected_tool,arg_key",
    [
        ("graphql", "web_graphql_test", "endpoint"),
        ("websocket", "web_websocket_test", "channel"),
        ("authz", "web_authz_test", "endpoint"),
    ],
)
def test_protocol_scenarios_emit_tools(
    monkeypatch,
    scenario: str,
    expected_tool: str,
    arg_key: str,
) -> None:
    monkeypatch.setenv("LAB_PROVIDER_SCENARIO", scenario)
    with TestClient(app) as client:
        first = client.post(
            "/v1/chat/completions",
            json={
                "model": "veridix-lab-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Target in scope: http://127.0.0.1:8000/graphql\n"
                            "Mission: test protocol once"
                        ),
                    }
                ],
            },
        )
        first_message = first.json()["choices"][0]["message"]
        assert (
            first_message["tool_calls"][0]["function"]["name"]
            == expected_tool
        )
        arguments = json.loads(
            first_message["tool_calls"][0]["function"]["arguments"]
        )
        assert arg_key in arguments


def test_ssrf_scenario_sequences_oast_tools(monkeypatch) -> None:
    monkeypatch.setenv("LAB_PROVIDER_SCENARIO", "ssrf")
    monkeypatch.setenv("LAB_OAST_BASE_URL", "http://127.0.0.1:8791")
    with TestClient(app) as client:
        base_messages = [
            {
                "role": "user",
                "content": "Target in scope: http://target\nMission: ssrf",
            }
        ]
        first = client.post(
            "/v1/chat/completions",
            json={"model": "veridix-lab-flash", "messages": base_messages},
        ).json()
        first_call = first["choices"][0]["message"]["tool_calls"][0]
        assert first_call["function"]["name"] == "oast_create"

        second = client.post(
            "/v1/chat/completions",
            json={
                "model": "veridix-lab-flash",
                "messages": [
                    *base_messages,
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [first_call],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_lab_probe",
                        "content": '{"token": "oast_abc"}',
                    },
                ],
            },
        ).json()
        second_call = second["choices"][0]["message"]["tool_calls"][0]
        assert second_call["function"]["name"] == "web_ssrf_test"
        callback_url = json.loads(
            second_call["function"]["arguments"]
        )["callback_url"]
        assert callback_url.endswith("/callback/oast_abc")

        third = client.post(
            "/v1/chat/completions",
            json={
                "model": "veridix-lab-flash",
                "messages": [
                    *base_messages,
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [first_call],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_lab_probe",
                        "content": '{"token": "oast_abc"}',
                    },
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [second_call],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_lab_probe",
                        "content": "ok",
                    },
                ],
            },
        ).json()
        third_call = third["choices"][0]["message"]["tool_calls"][0]
        assert third_call["function"]["name"] == "oast_check"
        assert (
            json.loads(third_call["function"]["arguments"])["token"]
            == "oast_abc"
        )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_http(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"endpoint did not become ready: {url}")


@pytest.mark.integration
def test_golden_run_against_local_lab_provider() -> None:
    root = Path(__file__).resolve().parents[2]
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.lab_provider.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    endpoint = f"http://127.0.0.1:{port}/v1"
    try:
        _wait_http(f"http://127.0.0.1:{port}/healthz")

        from services.agent_runtime.golden import GoldenRunDriver, GoldenRunSpec

        spec = GoldenRunSpec(
            run_id="golden_local_lab_001",
            mission="Use the shell.probe tool against the target once, then finish.",
            target_ref="https://lab.example.test",
            behavior_snapshot="behavior_local_lab_001",
            provider_endpoint=endpoint,
            provider_model="veridix-lab-flash",
            max_turns=5,
        )
        result = GoldenRunDriver(timeout_seconds=15).run(spec)

        assert result.status == "succeeded"
        assert result.oracle_passed is True
        assert result.evidence_refs
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
