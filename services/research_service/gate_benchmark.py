from __future__ import annotations

import io
import json
import time
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any

import httpx


def run_gate_benchmark(control_url: str = "http://127.0.0.1:8787") -> dict[str, Any]:
    """Run the end-to-end evidence gate benchmark against a control plane."""
    base = control_url.rstrip("/") + "/api/v1"
    key = uuid.uuid4().hex[:8]

    def call(method: str, path: str, body=None, timeout: float = 60.0):
        response = _request(base, method, path, body, timeout)
        if response.status_code >= 400:
            raise RuntimeError(
                f"{method} {path} -> {response.status_code}: "
                f"{response.text[:200]}"
            )
        return response

    project = call("POST", "/projects", {"name": f"gate-bench-{key}"}).json()
    target = call(
        "POST",
        f"/projects/{project['project_id']}/targets",
        {"url": "https://lab.example.test"},
    ).json()
    mission = call(
        "POST",
        "/missions",
        {
            "project_id": project["project_id"],
            "name": "evidence-gate-bench",
            "spec": {
                "target_id": target["target_id"],
                "mission": "gate benchmark",
                "max_turns": 1,
            },
        },
    ).json()
    run = call(
        "POST",
        f"/missions/{mission['mission_id']}/runs",
        {
            "mission_id": mission["mission_id"],
            "idempotency_key": f"gate-{key}",
        },
    ).json()
    run_id = run["run_id"]

    finding = call(
        "POST",
        f"/runs/{run_id}/findings",
        {
            "target_ref": "https://lab.example.test",
            "vuln_category": "xss",
            "endpoint": f"https://lab.example.test/?q=probe&gate={key}",
            "param": "q",
            "notes": "gate benchmark finding",
            "evidence": {
                "source_type": "external_scanner",
                "artifact_refs": ["artifacts/probe.txt"],
                "replay_proof": {
                    "request_id": "req-gate",
                    "replayed_status": 200,
                    "matched": True,
                },
                "confidence": 0.9,
                "occurred_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "action_ref": "web.replay",
                "tool_version": "gate-bench",
            },
        },
    ).json()
    finding_id = finding["finding_id"]
    call("POST", f"/findings/{finding_id}/support").json()
    call(
        "POST",
        f"/findings/{finding_id}/verify",
        {"oracle": "verified"},
    ).json()
    call(
        "POST",
        f"/findings/{finding_id}/retest",
        {"proof": {"matched": True, "replayed_status": 200}},
    ).json()

    gate = call("GET", f"/runs/{run_id}/evidence-gate").json()
    bundle = call(
        "GET",
        f"/runs/{run_id}/report-bundle",
        timeout=30,
    ).content
    sarif_found = _sarif_contains_rule(bundle, "xss")
    passed = (
        bool(gate.get("gate_pass"))
        and int(gate.get("replay_proven", 0)) >= 1
        and sarif_found
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "run_id": run_id,
        "gate": gate,
        "sarif_rule_found": sarif_found,
        "passed": passed,
    }
    call("DELETE", f"/projects/{project['project_id']}")
    return payload


def _request(
    base: str,
    method: str,
    path: str,
    body=None,
    timeout: float = 60.0,
) -> httpx.Response:
    last: Exception | None = None
    for _ in range(4):
        try:
            return httpx.request(
                method,
                f"{base}{path}",
                json=body,
                timeout=timeout,
            )
        except httpx.HTTPError as error:
            last = error
            time.sleep(1)
    raise last


def _sarif_contains_rule(bundle: bytes, rule_id: str) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            if "report.sarif" not in archive.namelist():
                return False
            sarif = json.loads(archive.read("report.sarif"))
            results = (
                sarif.get("runs", [{}])[0].get("results", [])
                if sarif.get("runs")
                else []
            )
            return any(
                str(result.get("ruleId", "")).lower() == rule_id.lower()
                for result in results
            )
    except Exception:
        return False
