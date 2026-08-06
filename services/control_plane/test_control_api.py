from __future__ import annotations

from fastapi.testclient import TestClient
import io
import json
import re
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from services.control_plane.app.control_store import ControlStore
from services.control_plane.app.contracts import AgentEvent
from services.control_plane.app.event_store import CommandStore, EventStore
from services.control_plane.app.main import create_app
from services.control_plane.app.run_service import RunService


def test_builtin_skill_catalog_is_rich_and_bundle_aware() -> None:
    with TestClient(create_app(":memory:")) as client:
        response = client.get("/api/v1/runtime/skills")
        assert response.status_code == 200
        rows = response.json()
        skill_dir = Path(__file__).resolve().parents[2] / "skills" / "builtin"
        expected_skills = len(list(skill_dir.glob("*/SKILL.md")))
        assert len(rows) == expected_skills
        assert len(rows) >= 40
        by_ref = {str(row["skill_ref"]): row for row in rows}

        orchestration = by_ref["veridix-redteam-orchestration"]
        assert orchestration["description"]
        assert orchestration["required_tools"]
        assert orchestration["package_path"].startswith("skills/builtin/")
        assert any(
            str(file).endswith("references/evidence-gate.md")
            for file in orchestration["files"]
        )

        sample = by_ref["strix-idor"]
        assert sample["description"]
        assert sample["package_path"].startswith("skills/builtin/")

        detail = client.get(
            "/api/v1/runtime/skills/veridix-redteam-orchestration"
        )
        assert detail.status_code == 200
        payload = detail.json()
        assert "Objective" in payload["content"]
        assert payload["package_path"].startswith("skills/builtin/")


def test_diagnostics_self_check() -> None:
    with TestClient(create_app(":memory:")) as client:
        response = client.post("/api/v1/diagnostics/self-check")

        assert response.status_code == 200
        payload = response.json()
        assert "checked_at" in payload
        assert "ok" in payload
        assert "components" in payload
        assert "counts" in payload
        assert "providers" in payload["counts"]


def test_run_lifecycle_and_event_cursor() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        target = client.post(
            f"/api/v1/projects/{project['project_id']}/targets",
            json={"url": "https://lab.example.test"},
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web discovery",
                "spec": {"target_id": target["target_id"]},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()

        assert run["status"] == "queued"
        assert run["event_count"] == 1
        run_id = run["run_id"]

        claimed = client.post(
            f"/api/v1/runs/{run_id}/claim",
            json={
                "worker_id": "agent-worker",
                "idempotency_key": "claim:1",
            },
        ).json()
        assert claimed["status"] == "running"

        paused = client.post(
            f"/api/v1/runs/{run_id}/pause",
            json={"idempotency_key": "pause:1"},
        ).json()
        assert paused["status"] == "paused"

        resumed = client.post(
            f"/api/v1/runs/{run_id}/resume",
            json={"idempotency_key": "resume:1"},
        ).json()
        assert resumed["status"] == "running"

        cancelled = client.post(
            f"/api/v1/runs/{run_id}/cancel",
            json={"idempotency_key": "cancel:1"},
        ).json()
        assert cancelled["status"] == "cancelled"

        events = client.get(
            f"/api/v1/runs/{run_id}/events",
            params={"after": 3},
        ).json()
        assert [event["event_type"] for event in events] == [
            "run.resumed",
            "run.cancelled",
        ]


def test_start_run_is_idempotent() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()

        first = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:same",
            },
        ).json()
        second = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:same",
            },
        ).json()

        assert second["run_id"] == first["run_id"]
        assert second["event_count"] == 1


def test_invalid_transition_returns_400() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()

        claimed = client.post(
            f"/api/v1/runs/{run['run_id']}/claim",
            json={
                "worker_id": "agent-worker",
                "idempotency_key": "claim:1",
            },
        )
        assert claimed.status_code == 200
        cancelled = client.post(
            f"/api/v1/runs/{run['run_id']}/cancel",
            json={"idempotency_key": "cancel:1"},
        )
        pause_after_cancel = client.post(
            f"/api/v1/runs/{run['run_id']}/pause",
            json={"idempotency_key": "pause:1"},
        )

        assert cancelled.status_code == 200
        assert pause_after_cancel.status_code == 400
        assert pause_after_cancel.json()["detail"] == "cannot run.pause run in state cancelled"


def test_restart_rebuilds_same_run_state(tmp_path) -> None:
    db_path = tmp_path / "control.sqlite3"

    def open_services():
        events = EventStore(db_path)
        commands = CommandStore(db_path)
        control = ControlStore(events, commands, db_path)
        runs = RunService(events, commands, control)
        return events, commands, control, runs

    events_1, commands_1, control_1, runs_1 = open_services()
    project = control_1.create_project("lab")
    mission = control_1.create_mission(project.project_id, "web", {})
    run = runs_1.start_run(mission.mission_id, "start:1")
    runs_1.claim(run.run_id, "agent-worker", "claim:1")
    events_1.close()
    commands_1.close()
    control_1.close()

    events_2, commands_2, control_2, runs_2 = open_services()
    rebuilt = control_2.get_run(run.run_id)

    assert rebuilt.status == "running"
    assert rebuilt.mission_id == mission.mission_id
    assert rebuilt.event_count == 2
    events_2.close()
    commands_2.close()
    control_2.close()


def test_delete_project_cascades_runs_and_events() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        assert len(client.get(f"/api/v1/runs/{run['run_id']}/events").json()) == 1

        deleted = client.delete(f"/api/v1/projects/{project['project_id']}")

        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert client.get(f"/api/v1/runs/{run['run_id']}").status_code == 404
        assert client.get("/api/v1/projects").json() == []


def test_resource_event_ingestion_updates_run_state() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()

        response = client.post(
            f"/api/v1/runs/{run['run_id']}/events",
            json={
                "event_id": "resource.recovered:browser_1:abc",
                "event_type": "resource.recovered",
                "actor": "runner",
                "payload": {
                    "resource_id": "browser_1",
                    "resource_type": "browser",
                    "action": "rebuild",
                    "reason": "resource_lost",
                    "reobserve_required": True,
                },
            },
        )

        assert response.status_code == 200
        assert response.json()["event_type"] == "resource.recovered"
        assert (
            client.get(f"/api/v1/runs/{run['run_id']}").json()["status"]
            == "attention_required"
        )
        event_types = [
            event["event_type"]
            for event in client.get(f"/api/v1/runs/{run['run_id']}/events").json()
        ]
        assert "resource.recovered" in event_types


def test_resource_event_ingestion_rejects_unauthorized_writers() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()

        bad_actor = client.post(
            f"/api/v1/runs/{run['run_id']}/events",
            json={
                "event_id": "resource.recovered:browser_1:bad",
                "event_type": "resource.recovered",
                "actor": "browser",
                "payload": {},
            },
        )
        bad_type = client.post(
            f"/api/v1/runs/{run['run_id']}/events",
            json={
                "event_id": "custom.event:bad",
                "event_type": "custom.event",
                "actor": "runner",
                "payload": {},
            },
        )
        missing_run = client.post(
            "/api/v1/runs/run_missing/events",
            json={
                "event_id": "resource.recovered:browser_1:missing",
                "event_type": "resource.recovered",
                "actor": "runner",
                "payload": {},
            },
        )

        assert bad_actor.status_code == 403
        assert bad_type.status_code == 403
        assert missing_run.status_code == 404


def test_approval_event_carries_reason() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()

        client.post(
            f"/api/v1/runs/{run['run_id']}/approvals",
            json={
                "tool_ref": "shell.exec",
                "risk_level": "L2",
                "idempotency_key": "approval:1",
                "reason": "long response drill",
            },
        )

        events = client.get(f"/api/v1/runs/{run['run_id']}/events").json()
        approval_events = [
            event for event in events if event["event_type"] == "approval.requested"
        ]
        assert len(approval_events) == 1
        assert approval_events[0]["payload"]["reason"] == "long response drill"


def test_tool_failed_event_is_accepted() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()

        response = client.post(
            f"/api/v1/runs/{run['run_id']}/events",
            json={
                "event_id": "tool.failed:shell.exec:abc",
                "event_type": "tool.failed",
                "actor": "agent-worker",
                "payload": {
                    "tool": "shell.exec",
                    "exit_code": 1,
                    "stderr": "boom",
                },
            },
        )

        assert response.status_code == 200
        event_types = [
            event["event_type"]
            for event in client.get(f"/api/v1/runs/{run['run_id']}/events").json()
        ]
        assert "tool.failed" in event_types


def test_web_observations_upsert_list_and_cascade() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        run_id = run["run_id"]
        observation = {
            "request_id": "req_0001",
            "web_session_id": "web_1",
            "proxy_session_id": "proxy_1",
            "method": "GET",
            "url": "https://lab.example.test/admin",
            "endpoint": "GET /admin",
            "status_code": 200,
            "request_headers": {},
            "response_headers": {"content-type": "text/html"},
            "request_body": "",
            "response_body": "<html>admin</html>",
            "content_type": "text/html",
            "request_size": 0,
            "response_size": 20,
            "artifact_ref": "artifact://capture/req_0001/raw",
            "redacted": False,
            "truncated": False,
            "replay_proof": {
                "request_id": "req_0001",
                "request_fingerprint": "sha256:req",
                "response_fingerprint": "sha256:res",
                "replayed_status": 200,
                "replayed_at": "2026-08-01T00:00:00Z",
                "matched": True,
            },
        }

        stored = client.post(
            f"/api/v1/runs/{run_id}/web-observations",
            json={"observations": [observation]},
        )
        assert stored.status_code == 200
        assert stored.json()["stored"] == 1

        rows = client.get(
            f"/api/v1/runs/{run_id}/web-observations"
        ).json()
        assert rows[0]["request_id"] == "req_0001"
        assert rows[0]["method"] == "GET"
        assert rows[0]["status_code"] == 200
        assert rows[0]["replay_proof"]["matched"] is True

        missing = client.post(
            "/api/v1/runs/run_missing/web-observations",
            json={"observations": [observation]},
        )
        assert missing.status_code == 404

        deleted = client.delete(f"/api/v1/projects/{project['project_id']}")
        assert deleted.status_code == 200
        assert client.app.state.web_observations.count() == 0


def test_finding_lifecycle_via_api() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        run_id = run["run_id"]

        finding = client.post(
            f"/api/v1/runs/{run_id}/findings",
            json={
                "target_ref": "https://lab.example.test",
                "vuln_category": "authz",
                "endpoint": "/admin",
                "param": "role",
            },
        ).json()
        assert finding["run_id"] == run_id
        assert finding["status"] == "candidate"

        supported = client.post(
            f"/api/v1/findings/{finding['finding_id']}/support"
        )
        assert supported.status_code == 200
        assert supported.json()["status"] == "supported"

        fetched = client.get(
            f"/api/v1/findings/{finding['finding_id']}"
        )
        assert fetched.status_code == 200
        assert fetched.json()["finding_id"] == finding["finding_id"]
        assert fetched.json()["status"] == "supported"

        verified = client.post(
            f"/api/v1/findings/{finding['finding_id']}/verify",
            json={"oracle": "verified"},
        )
        assert verified.json()["status"] == "verified"

        reviewed = client.post(
            f"/api/v1/findings/{finding['finding_id']}/review",
            json={"decision": "open", "decided_by": "operator"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "open"

        retested = client.post(
            f"/api/v1/findings/{finding['finding_id']}/retest",
            json={"proof": {"matched": True, "replayed_status": 200}},
        )
        assert retested.status_code == 200
        assert retested.json()["status"] == "retest_passed"

        rows = client.get(f"/api/v1/runs/{run_id}/findings").json()
        assert len(rows) == 1
        assert rows[0]["status"] == "retest_passed"

        missing = client.post(
            "/api/v1/findings/finding_missing/review",
            json={"decision": "open", "decided_by": "operator"},
        )
        assert missing.status_code == 404


def test_finding_with_evidence_persists_and_supports_hash_verified() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        run_id = run["run_id"]

        finding = client.post(
            f"/api/v1/runs/{run_id}/findings",
            json={
                "target_ref": "https://lab.example.test",
                "vuln_category": "SQL Injection",
                "endpoint": "https://lab.example.test/?id=1",
                "evidence": {
                    "source_type": "external_scanner",
                    "artifact_refs": ["artifact://caido/11"],
                    "action_ref": "caido:11",
                    "confidence": 0.6,
                    "parser_version": "1",
                },
            },
        ).json()

        assert len(finding["evidence_ids"]) == 1
        evidence_id = finding["evidence_ids"][0]
        evidence = client.app.state.evidence.evidence_map()[evidence_id]
        assert evidence.source_type == "external_scanner"
        assert evidence.compute_hash() == evidence.hash

        supported = client.post(
            f"/api/v1/findings/{finding['finding_id']}/support"
        )
        assert supported.status_code == 200
        assert supported.json()["status"] == "supported"

        evidence_rows = client.get(
            f"/api/v1/runs/{run_id}/evidence"
        ).json()
        assert len(evidence_rows) == 1
        assert evidence_rows[0]["source_type"] == "external_scanner"


def test_diagnostics_include_tool_environment_and_graph_metrics(
    tmp_path,
    monkeypatch,
) -> None:
    (tmp_path / "tool-environment.json").write_text(
        json.dumps(
            {
                "builder_version": "tool-env-1",
                "digest": "env_digest_123",
                "packs": [{"name": "web"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "storage.json").write_text(
        json.dumps(
            {
                "embedding": {"backend": "local", "model": "fixture-model"},
                "vector_store": {"type": "sqlite"},
                "graph": {"enabled": True, "backend": "sqlite"},
                "rerank": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VERIDIX_RUNTIME_DIR", str(tmp_path))
    with TestClient(create_app(":memory:")) as client:
        diagnostics = client.get("/api/v1/diagnostics").json()

        assert diagnostics["tool_environment"]["available"] is True
        assert diagnostics["tool_environment"]["digest"] == "env_digest_123"
        assert diagnostics["storage"]["available"] is True
        assert diagnostics["storage"]["vector_store"]["type"] == "sqlite"
        assert re.fullmatch(r"[0-9a-f]{64}", diagnostics["product_identity"]["digest"])
        assert set(diagnostics["connectors"]) == {
            "zap",
            "caido",
            "burp",
        }

        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        run_id = run["run_id"]
        client.app.state.events.append(
            AgentEvent(
                event_id="ev_graph",
                event_type="graph.completed",
                stream_id=run_id,
                run_id=run_id,
                actor="agent-worker",
                payload={"handoffs": 2, "dead_letters": 0},
            )
        )

        metrics = client.get(
            f"/api/v1/runs/{run_id}/graph-metrics"
        ).json()

        assert metrics["graph_completed"] == 1
        assert metrics["metrics"][0]["handoffs"] == 2


def test_run_trace_aggregates_events_findings_and_metrics() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "trace"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "trace",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "trace:start",
            },
        ).json()
        run_id = run["run_id"]
        for event in (
            ("tool.started", {"tool": "nmap.scan"}),
            ("memory.fact.appended", {}),
            ("graph.human.required", {"node_id": "verifier"}),
            ("graph.completed", {"handoffs": 1, "dead_letters": 0}),
        ):
            client.app.state.events.append(
                AgentEvent(
                    event_id=f"trace:{event[0]}",
                    event_type=event[0],
                    stream_id=run_id,
                    run_id=run_id,
                    actor="agent-worker",
                    payload=event[1],
                )
            )

        trace = client.get(f"/api/v1/runs/{run_id}/trace").json()

        assert trace["event_count"] >= 4
        assert "run.queued" in trace["event_types"]
        assert trace["approval_required"] == 1
        assert trace["memory_facts_appended"] == 1
        assert trace["graph_metrics"][0]["handoffs"] == 1
        assert trace["tool_events"][0]["payload"]["tool"] == "nmap.scan"


def test_append_finding_note_appends_to_finding() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "notes"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "notes",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "notes:start",
            },
        ).json()
        finding = client.post(
            f"/api/v1/runs/{run['run_id']}/findings",
            json={
                "target_ref": "https://lab.example.test",
                "vuln_category": "XSS",
                "endpoint": "/xss",
            },
        ).json()

        updated = client.post(
            f"/api/v1/findings/{finding['finding_id']}/notes",
            json={"note": "LLM judge: verify manually"},
        ).json()

        assert "LLM judge: verify manually" in updated["notes"]


def test_diagnostics_tool_environment_accepts_real_payload_shape(
    tmp_path,
    monkeypatch,
) -> None:
    (tmp_path / "tool-environment.json").write_text(
        json.dumps(
            {
                "available": True,
                "image": "veridix-tools:full",
                "digest": "env_digest_456",
                "packs": ["network", "web", "vulnscan"],
                "health": "ok",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VERIDIX_RUNTIME_DIR", str(tmp_path))
    with TestClient(create_app(":memory:")) as client:
        diagnostics = client.get("/api/v1/diagnostics").json()
    assert diagnostics["tool_environment"] == {
        "available": True,
        "digest": "env_digest_456",
        "builder_version": "",
        "packs": ["network", "web", "vulnscan"],
        "health": "ok",
    }


def test_knowledge_import_endpoint_splits_markdown() -> None:
    with TestClient(create_app(":memory:")) as client:
        response = client.post(
            "/api/v1/knowledge/import",
            json={
                "source_id": "manual/imported",
                "license": "CC-BY-4.0",
                "version": "1.0.0",
                "content": (
                    "# Playbook\n\n"
                    "## IDOR Check\n\nUse two auth contexts.\n\n"
                    "## SSRF Check\n\nUse a one-time callback token.\n"
                ),
                "subjects": ["web_test"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["chunk_count"] == 2
        assert data["license"] == "CC-BY-4.0"
        sources = client.get("/api/v1/knowledge/sources").json()
        assert sources[0]["source_id"] == "manual/imported"
        chunks = client.get("/api/v1/knowledge").json()
        assert len(chunks) == 2


def test_run_attack_graph_endpoint() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        run_id = run["run_id"]
        client.post(
            f"/api/v1/runs/{run_id}/findings",
            json={
                "target_ref": "https://lab.example.test",
                "vuln_category": "IDOR",
                "endpoint": "/admin",
            },
        )

        graph = client.get(
            f"/api/v1/runs/{run_id}/attack-graph"
        ).json()

        assert any(
            node["kind"] == "vulnerability"
            and node["id"] == "vuln:IDOR"
            for node in graph["nodes"]
        )
        assert any(
            edge["predicate"] == "exposes"
            for edge in graph["edges"]
        )


def test_human_gate_endpoints_list_and_resolve() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        run_id = run["run_id"]
        client.app.state.events.append(
            AgentEvent(
                event_id="hg_1",
                event_type="graph.human.required",
                stream_id=run_id,
                run_id=run_id,
                actor="agent-worker",
                payload={"node_id": "gate", "prompt": "approve?"},
            )
        )

        gates = client.get(
            f"/api/v1/runs/{run_id}/human-gates"
        ).json()
        assert gates["pending"][0]["node_id"] == "gate"

        resolved = client.post(
            f"/api/v1/runs/{run_id}/human-gates/gate/resolve",
            json={"approved": True, "reason": "operator-ok"},
        ).json()
        assert resolved["approved"] is True

        gates_after = client.get(
            f"/api/v1/runs/{run_id}/human-gates"
        ).json()
        assert gates_after["pending"] == []
        assert gates_after["resolved"]["gate"]["approved"] is True


def test_diagnostics_probe_connector_health(
    tmp_path,
    monkeypatch,
) -> None:
    class ConnectorHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._send({"ok": True})

        def do_POST(self) -> None:
            self._send({"data": {"__typename": "Query"}})

        def _send(self, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ConnectorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setenv("VERIDIX_ZAP_URL", base)
        monkeypatch.setenv("VERIDIX_CAIDO_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("VERIDIX_BURP_URL", base)
        with TestClient(create_app(":memory:")) as client:
            connectors = client.get(
                "/api/v1/diagnostics"
            ).json()["connectors"]

            assert connectors["zap"]["status"] == "ok"
            assert connectors["burp"]["status"] == "ok"
            assert connectors["caido"]["status"] == "unreachable"
            assert (
                client.app.state.control.get_connector_status("zap")["status"]
                == "ok"
            )
    finally:
        server.shutdown()
        server.server_close()


def test_artifact_endpoint_returns_stored_bytes() -> None:
    with TestClient(create_app(":memory:")) as client:
        artifact = client.app.state.artifact_store.put(
            b"nuclei-jsonl",
            content_type="application/json",
        )

        response = client.get(
            f"/api/v1/artifacts/{artifact.artifact_id}"
        )

        assert response.status_code == 200
        assert response.content == b"nuclei-jsonl"


def test_artifact_preview_truncates_large_content() -> None:
    with TestClient(create_app(":memory:")) as client:
        artifact = client.app.state.artifact_store.put(
            b"x" * 2000,
            content_type="text/plain",
        )

        response = client.get(
            f"/api/v1/artifacts/{artifact.artifact_id}",
            params={"preview": "true", "max_bytes": 100},
        )

        payload = response.json()
        assert payload["truncated"] is True
        assert len(payload["preview"]) == 100


def test_knowledge_api_add_list_search() -> None:
    with TestClient(create_app(":memory:")) as client:
        added = client.post(
            "/api/v1/knowledge",
            json={
                "chunk_id": "k_admin",
                "source_ref": "api-test",
                "content": "admin panel default credentials",
                "subjects": ["web"],
                "target_refs": ["https://lab.example.test"],
                "observed_at": "2026-08-01T00:00:00Z",
            },
        )
        assert added.status_code == 200
        assert added.json()["revision"] == 1

        listed = client.get("/api/v1/knowledge").json()
        assert listed[0]["chunk_id"] == "k_admin"
        assert listed[0]["target_refs"] == ["https://lab.example.test"]
        assert listed[0]["observed_at"] == "2026-08-01T00:00:00Z"

        searched = client.get(
            "/api/v1/knowledge/search",
            params={"q": "admin panel"},
        ).json()
        assert searched[0]["chunk_id"] == "k_admin"

        updated = client.put(
            "/api/v1/knowledge/k_admin",
            json={
                "chunk_id": "k_admin",
                "source_ref": "api-test",
                "content": "admin panel default credentials updated",
                "subjects": ["web"],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["revision"] == 2

        deleted = client.delete("/api/v1/knowledge/k_admin")
        assert deleted.status_code == 200
        assert client.get("/api/v1/knowledge").json() == []

        audit = client.app.state.events.replay("knowledge")
        assert [event.event_type for event in audit] == [
            "knowledge.added",
            "knowledge.updated",
            "knowledge.deleted",
        ]
        api_audit = client.get("/api/v1/knowledge/events").json()
        assert api_audit["total"] == 3
        assert api_audit["events"][0]["event_type"] == "knowledge.added"
        filtered = client.get(
            "/api/v1/knowledge/events",
            params={"chunk_id": "k_admin", "limit": 1},
        ).json()
        assert filtered["total"] == 3
        assert len(filtered["events"]) == 1


def test_knowledge_api_search_target_ref_and_time_filter() -> None:
    with TestClient(create_app(":memory:")) as client:
        client.post(
            "/api/v1/knowledge",
            json={
                "chunk_id": "k_lab_old",
                "source_ref": "api-test",
                "content": "target scoped knowledge",
                "target_refs": ["https://lab.example.test"],
                "observed_at": "2026-07-01T00:00:00Z",
            },
        )
        client.post(
            "/api/v1/knowledge",
            json={
                "chunk_id": "k_lab_new",
                "source_ref": "api-test",
                "content": "target scoped knowledge",
                "target_refs": ["https://lab.example.test"],
                "observed_at": "2026-08-01T00:00:00Z",
            },
        )
        client.post(
            "/api/v1/knowledge",
            json={
                "chunk_id": "k_other",
                "source_ref": "api-test",
                "content": "target scoped knowledge",
                "target_refs": ["https://other.example.test"],
                "observed_at": "2026-08-01T00:00:00Z",
            },
        )

        raw = client.get(
            "/api/v1/knowledge/search",
            params={
                "q": "target scoped",
                "target_ref": "https://lab.example.test",
                "observed_since": "2026-07-15T00:00:00Z",
                "observed_until": "2026-08-15T00:00:00Z",
            },
        ).json()
        scoped = [row for row in raw if "chunk_id" in row]

        assert [row["chunk_id"] for row in scoped] == ["k_lab_new"]
        assert scoped[0]["target_refs"] == ["https://lab.example.test"]


def test_memory_api_view_fix_forget_clear() -> None:
    with TestClient(create_app(":memory:")) as client:
        memory = client.app.state.memory.get("default")
        recorded = client.post(
            "/api/v1/memory/record",
            json={
                "subject": "/api",
                "predicate": "reachable",
                "value": "true",
                "trust": "user_approved",
            },
        )
        assert recorded.status_code == 200
        assert recorded.json()["inserted"] is True
        assert recorded.json()["trust"] == "user_approved"

        first, _ = memory.record(
            "/admin",
            "accepts_role",
            "owner",
            trust="project_observed",
        )
        memory.record(
            "/admin",
            "accepts_role",
            "user",
            trust="project_observed",
        )

        listed = client.get("/api/v1/memory").json()
        assert listed["snapshot"]["conflict"] >= 1
        assert any(row["predicate"] == "accepts_role" for row in listed["facts"])

        fixed = client.post(
            "/api/v1/memory/fix",
            json={
                "subject": "/admin",
                "predicate": "accepts_role",
                "value": "owner_only",
                "reason": "verified_by_admin",
            },
        )
        assert fixed.status_code == 200
        assert fixed.json()["value"] == "owner_only"
        assert fixed.json()["trust"] == "human"

        forgotten = client.post(
            f"/api/v1/memory/{first.fact_id}/forget",
            json={"reason": "replaced_by_fix"},
        )
        assert forgotten.status_code == 200
        assert forgotten.json()["forgotten"] == first.fact_id

        cleared = client.post(
            "/api/v1/memory/clear",
            json={"reason": "review_complete"},
        )
        assert cleared.status_code == 200
        assert cleared.json()["cleared"] >= 1


def test_knowledge_api_project_filter() -> None:
    with TestClient(create_app(":memory:")) as client:
        client.post(
            "/api/v1/knowledge",
            json={
                "chunk_id": "k_p1",
                "source_ref": "api-test",
                "content": "project one knowledge",
                "project_id": "project_1",
                "subjects": ["web"],
            },
        )
        client.post(
            "/api/v1/knowledge",
            json={
                "chunk_id": "k_p2",
                "source_ref": "api-test",
                "content": "project two knowledge",
                "project_id": "project_2",
                "subjects": ["web"],
            },
        )

        listed = client.get(
            "/api/v1/knowledge",
            params={"project_id": "project_1"},
        ).json()
        assert [row["chunk_id"] for row in listed] == ["k_p1"]
        assert listed[0]["project_id"] == "project_1"

        searched = client.get(
            "/api/v1/knowledge/search",
            params={"q": "knowledge", "project_id": "project_2"},
        ).json()
        assert [row["chunk_id"] for row in searched if "chunk_id" in row] == [
            "k_p2"
        ]


def test_knowledge_graph_endpoint_returns_bounded_snapshot() -> None:
    with TestClient(create_app(":memory:")) as client:
        graph = client.app.state.knowledge_graph
        graph.upsert_node(
            "attck.T1003",
            node_type="technique",
            label="Credential Access",
        )
        graph.upsert_node(
            "cwe.79",
            node_type="cwe",
            label="Cross-site Scripting",
        )
        graph.upsert_edge("attck.T1003", "cwe.79", "related_to")

        payload = client.get("/api/v1/knowledge/graph").json()

        assert payload["counts"]["nodes"] == 2
        assert payload["counts"]["edges"] == 1
        assert {node["label"] for node in payload["nodes"]} == {
            "Credential Access",
            "Cross-site Scripting",
        }
        assert payload["edges"][0]["predicate"] == "related_to"


def test_merged_findings_view_via_api() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        run_id = run["run_id"]
        body = {
            "target_ref": "https://lab.example.test",
            "vuln_category": "authz",
            "endpoint": "/admin",
            "param": "role",
        }
        first = client.post(
            f"/api/v1/runs/{run_id}/findings", json=body
        ).json()
        second = client.post(
            f"/api/v1/runs/{run_id}/findings", json=body
        ).json()

        views = client.get(
            f"/api/v1/runs/{run_id}/findings/merged"
        ).json()

        assert second["status"] == "duplicate"
        assert len(views) == 1
        assert views[0]["duplicate_count"] == 1
        assert set(views[0]["source_finding_ids"]) == {
            first["finding_id"],
            second["finding_id"],
        }


def test_evidence_gate_counts_verified_duplicate_fingerprint() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "gate"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "gate",
                "spec": {},
            },
        ).json()
        run_one = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "gate:1",
            },
        ).json()["run_id"]
        run_two = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "gate:2",
            },
        ).json()["run_id"]
        body = {
            "target_ref": "http://compose-dvwa-1:80",
            "vuln_category": "Exposure",
            "endpoint": "compose-dvwa-1:80",
            "evidence": {
                "source_type": "structured_scan",
                "action_ref": "scan/1",
                "artifact_refs": ["artifact://scan/1"],
                "confidence": 0.8,
            },
        }
        first = client.post(
            f"/api/v1/runs/{run_one}/findings", json=body
        ).json()
        client.post(
            f"/api/v1/findings/{first['finding_id']}/support"
        )
        client.post(
            f"/api/v1/findings/{first['finding_id']}/verify",
            json={"oracle": "verified"},
        )
        duplicate = client.post(
            f"/api/v1/runs/{run_two}/findings", json=body
        ).json()
        assert duplicate["status"] == "duplicate"

        gate = client.get(
            f"/api/v1/runs/{run_two}/evidence-gate"
        ).json()

        assert gate["gate_pass"] is True
        assert gate["verified"] == 1


def test_reports_summary_batch_endpoint() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "reports"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "reports",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "reports:1",
            },
        ).json()["run_id"]
        body = {
            "target_ref": "http://compose-dvwa-1:80",
            "vuln_category": "Exposure",
            "endpoint": "compose-dvwa-1:80",
            "evidence": {
                "source_type": "structured_scan",
                "action_ref": "scan/report",
                "artifact_refs": ["artifact://scan/report"],
                "confidence": 0.8,
            },
        }
        finding = client.post(
            f"/api/v1/runs/{run}/findings", json=body
        ).json()
        client.post(
            f"/api/v1/findings/{finding['finding_id']}/support"
        )
        client.post(
            f"/api/v1/findings/{finding['finding_id']}/verify",
            json={"oracle": "verified"},
        )

        summary = client.get("/api/v1/reports/summary").json()
        row = next(
            item for item in summary["rows"] if item["run_id"] == run
        )

        assert summary["total"] == 1
        assert row["findings"] == 1
        assert row["verified"] == 1
        assert row["gate_pass"] is True
        assert row["sources"].get("unknown", 0) == 1


def test_list_missions_batch_endpoint() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "missions"}
        ).json()
        client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "m1",
                "spec": {"mission": "one"},
            },
        )
        client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "m2",
                "spec": {"mission": "two"},
            },
        )

        rows = client.get("/api/v1/missions").json()

        assert len(rows) == 2
        assert {row["name"] for row in rows} == {"m1", "m2"}


def test_remote_node_registry_api_register_heartbeat_and_results() -> None:
    with TestClient(create_app(":memory:")) as client:
        registered = client.post(
            "/api/v1/remote/nodes",
            json={
                "node_id": "agent-node-1",
                "version": "0.1.0",
                "capabilities": ["container", "ssh"],
                "public_key": "pubkey-fixture",
            },
        ).json()
        assert registered["status"] == "offline"

        heartbeat = client.post(
            "/api/v1/remote/nodes/agent-node-1/heartbeat",
            json={"lease_seconds": 300},
        ).json()
        assert heartbeat["status"] == "online"
        assert heartbeat["last_seen_at"]

        result = client.post(
            "/api/v1/remote/nodes/agent-node-1/results",
            json={
                "task_ref": "task/1",
                "status": "completed",
                "artifact_refs": ["artifact://node/1"],
                "signature": "sig-1",
                "payload": {"ok": True},
            },
        ).json()
        assert result["task_ref"] == "task/1"

        lease = client.post(
            "/api/v1/remote/nodes/agent-node-1/leases",
            json={"task_ref": "task/2", "lease_seconds": 300},
        ).json()
        assert lease["node_id"] == "agent-node-1"
        assert lease["task_ref"] == "task/2"

        dispatch = client.post(
            "/api/v1/remote/nodes/agent-node-1/dispatch",
            json={
                "task_ref": "task/3",
                "payload": {"tool": "nmap.scan", "target": "10.0.0.1"},
                "lease_seconds": 300,
            },
        ).json()
        assert dispatch["dispatch"]["node_id"] == "agent-node-1"
        assert dispatch["dispatch"]["task_ref"] == "task/3"
        assert dispatch["dispatch"]["payload"]["tool"] == "nmap.scan"
        assert dispatch["lease"]["lease_id"]

        tasks = client.get(
            "/api/v1/remote/nodes/agent-node-1/tasks"
        ).json()
        assert len(tasks) == 1
        assert tasks[0]["task_ref"] == "task/3"
        assert tasks[0]["payload"]["tool"] == "nmap.scan"

        completed = client.post(
            "/api/v1/remote/nodes/agent-node-1/results",
            json={
                "task_ref": "task/3",
                "status": "completed",
                "signature": "sig-3",
                "payload": {"stdout": "open 80/tcp"},
            },
        ).json()
        assert completed["task_ref"] == "task/3"
        tasks_after = client.get(
            "/api/v1/remote/nodes/agent-node-1/tasks"
        ).json()
        assert tasks_after == []

        results = client.get(
            "/api/v1/remote/nodes/agent-node-1/results"
        ).json()
        assert len(results) == 2
        assert {item["task_ref"] for item in results} == {
            "task/1",
            "task/3",
        }

        nodes = client.get("/api/v1/remote/nodes").json()
        assert len(nodes) == 1
        assert nodes[0]["capabilities"] == ["container", "ssh"]


def test_report_bundle_download_includes_artifact() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        run_id = run["run_id"]
        artifact = client.app.state.artifact_store.put(
            b"artifact-bytes",
            content_type="text/plain",
        )
        from services.evidence_service.models import Evidence

        evidence = Evidence(
            evidence_id="ev_api_bundle",
            source_type="web.replay",
            target_ref="https://lab.example.test",
            action_ref="proxy.replay",
            artifact_refs=[f"artifact://sha256/{artifact.artifact_id}"],
            replay_proof={},
            confidence=0.8,
        )
        client.app.state.evidence.submit_candidate(
            run_id=run_id,
            target_ref="https://lab.example.test",
            vuln_category="authz",
            endpoint="/admin",
            evidence=evidence,
        )

        response = client.get(f"/api/v1/runs/{run_id}/report-bundle")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            names = bundle.namelist()
            assert "report.json" in names
            assert f"artifacts/{artifact.artifact_id}" in names
            assert (
                bundle.read(f"artifacts/{artifact.artifact_id}")
                == b"artifact-bytes"
            )


def test_api_token_required_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("VERIDIX_CONTROL_TOKEN", "local-token")
    with TestClient(create_app(":memory:")) as client:
        denied = client.get("/api/v1/projects")
        assert denied.status_code == 401

        allowed = client.get(
            "/api/v1/projects",
            headers={"Authorization": "Bearer local-token"},
        )
        assert allowed.status_code == 200


def test_project_owner_recorded() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects",
            json={"name": "lab", "owner": "alice"},
        ).json()

        assert project["owner"] == "alice"
        listed = client.get("/api/v1/projects").json()
        assert listed[0]["owner"] == "alice"


def test_runtime_registry_endpoints() -> None:
    with TestClient(create_app(":memory:")) as client:
        runner = client.post(
            "/api/v1/runtime/runners",
            json={"runner_id": "container", "kind": "docker", "status": "online"},
        ).json()
        provider = client.post(
            "/api/v1/runtime/providers",
            json={
                "provider_id": "deepseek",
                "model": "deepseek-v4-flash",
                "endpoint": "https://api.deepseek.com",
            },
        ).json()
        tool = client.post(
            "/api/v1/runtime/tools",
            json={"tool_ref": "shell.probe", "capability": "probe"},
        ).json()

        assert runner["status"] == "online"
        assert provider["model"] == "deepseek-v4-flash"
        assert tool["tool_ref"] == "shell.probe"
        assert [item["runner_id"] for item in client.get("/api/v1/runtime/runners").json()] == [
            "container"
        ]
        assert len(client.get("/api/v1/runtime/providers").json()) == 1
        assert len(client.get("/api/v1/runtime/tools").json()) == 1


def test_loop_profiles_api_exposes_declarative_contracts() -> None:
    with TestClient(create_app(":memory:")) as client:
        profiles = client.get("/api/v1/runtime/loop-profiles").json()
        presets = client.get("/api/v1/runtime/loop-presets").json()

    assert "web_discovery" in profiles
    assert "verifier" in profiles
    assert "authz_matrix" in profiles
    assert profiles["web_discovery"]["oracle"] == "coverage_oracle"
    assert profiles["web_discovery"]["evidence_requirements"]
    assert profiles["verifier"]["success_criteria"] == (
        "all_candidates_replayed_or_inconclusive"
    )
    assert "nikto-focused" in presets
    assert "scanner" in presets["nikto-focused"]["loop_overrides"]


def test_acceptance_summary_includes_profile_engineering() -> None:
    with TestClient(create_app(":memory:")) as client:
        payload = client.get("/api/v1/acceptance").json()

    profile = payload.get("profile_engineering") or {}
    assert profile.get("preset_count", 0) >= 7
    assert "deterministic" in profile
    assert "real_preset" in profile
    assert "host-recon" in profile.get("real_presets", {})
    assert profile.get("external_fixture", {}).get("real_environment") == (
        "pending"
    )
    assert profile.get("preset_fixtures", {}).get("preset_count") >= 10


def test_harness_snapshot_event_is_accepted() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()

        response = client.post(
            f"/api/v1/runs/{run['run_id']}/events",
            json={
                "event_id": "harness.snapshot:run_1:abc",
                "event_type": "harness.snapshot",
                "actor": "agent-worker",
                "payload": {
                    "harness_digest": "h",
                    "behavior_snapshot": "b",
                },
            },
        )

        assert response.status_code == 200
        event_types = [
            event["event_type"]
            for event in client.get(f"/api/v1/runs/{run['run_id']}/events").json()
        ]
        assert "harness.snapshot" in event_types


def test_fork_run_creates_derived_run() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()

        fork = client.post(
            f"/api/v1/runs/{run['run_id']}/fork",
            json={"idempotency_key": "fork:1"},
        )

        assert fork.status_code == 200
        forked = fork.json()
        assert forked["source_run_id"] == run["run_id"]
        assert forked["status"] == "queued"
        source_events = client.get(
            f"/api/v1/runs/{run['run_id']}/events"
        ).json()
        assert any(
            event["event_type"] == "run.forked"
            and event["payload"]["forked_run_id"] == forked["run_id"]
            for event in source_events
        )

        replayed = client.post(
            f"/api/v1/runs/{run['run_id']}/fork",
            json={"idempotency_key": "fork:1"},
        )
        assert replayed.json()["run_id"] == forked["run_id"]


def test_takeover_pauses_run_and_records_owner() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()

        claimed = client.post(
            f"/api/v1/runs/{run['run_id']}/claim",
            json={
                "worker_id": "agent-worker",
                "idempotency_key": "claim:1",
            },
        )
        assert claimed.status_code == 200

        taken = client.post(
            f"/api/v1/runs/{run['run_id']}/takeover",
            json={
                "idempotency_key": "takeover:1",
                "taken_by": "operator",
                "reason": "manual review",
            },
        )

        assert taken.status_code == 200
        assert taken.json()["status"] == "paused"
        events = client.get(
            f"/api/v1/runs/{run['run_id']}/events"
        ).json()
        assert any(
            event["event_type"] == "run.taken_over"
            and event["payload"]["taken_by"] == "operator"
            for event in events
        )
        assert any(event["event_type"] == "run.paused" for event in events)

        replayed = client.post(
            f"/api/v1/runs/{run['run_id']}/takeover",
            json={
                "idempotency_key": "takeover:1",
                "taken_by": "operator",
                "reason": "manual review",
            },
        )
        assert replayed.json()["status"] == "paused"

        cancelled = client.post(
            f"/api/v1/runs/{run['run_id']}/cancel",
            json={"idempotency_key": "cancel:1"},
        )
        invalid = client.post(
            f"/api/v1/runs/{run['run_id']}/takeover",
            json={
                "idempotency_key": "takeover:2",
                "taken_by": "operator",
                "reason": "too late",
            },
        )
        assert cancelled.json()["status"] == "cancelled"
        assert invalid.status_code == 400


def test_diagnostics_reports_worker_lease_and_registry() -> None:
    with TestClient(create_app(":memory:")) as client:
        before = client.get("/api/v1/diagnostics").json()
        assert before["worker"]["status"] == "lost"

        client.post(
            "/api/v1/leases/agent-worker/heartbeat",
            json={"lease_seconds": 60},
        )
        client.post(
            "/api/v1/runtime/runners",
            json={"runner_id": "browser", "kind": "playwright"},
        )

        after = client.get("/api/v1/diagnostics").json()
        assert after["worker"]["status"] == "online"
        assert after["runners"][0]["runner_id"] == "browser"


def test_claim_run_is_idempotent_and_rejects_running() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        run_id = run["run_id"]

        first = client.post(
            f"/api/v1/runs/{run_id}/claim",
            json={"worker_id": "agent-worker", "idempotency_key": "claim:1"},
        )
        second = client.post(
            f"/api/v1/runs/{run_id}/claim",
            json={"worker_id": "agent-worker", "idempotency_key": "claim:1"},
        )
        rejected = client.post(
            f"/api/v1/runs/{run_id}/claim",
            json={"worker_id": "runner-2", "idempotency_key": "claim:2"},
        )

        assert first.status_code == 200
        assert first.json()["status"] == "running"
        assert second.status_code == 200
        assert rejected.status_code == 400


def test_finish_run_transitions_and_is_idempotent() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()
        run_id = run["run_id"]
        client.post(
            f"/api/v1/runs/{run_id}/claim",
            json={"worker_id": "agent-worker", "idempotency_key": "claim:1"},
        )

        finished = client.post(
            f"/api/v1/runs/{run_id}/finish",
            json={
                "outcome": "succeeded",
                "idempotency_key": "finish:1",
                "stop_reason": "run.finish",
                "summary": "verified",
            },
        )
        replayed = client.post(
            f"/api/v1/runs/{run_id}/finish",
            json={
                "outcome": "succeeded",
                "idempotency_key": "finish:1",
                "stop_reason": "run.finish",
                "summary": "verified",
            },
        )
        invalid = client.post(
            f"/api/v1/runs/{run_id}/finish",
            json={
                "outcome": "failed",
                "idempotency_key": "finish:2",
            },
        )

        assert finished.status_code == 200
        assert finished.json()["status"] == "succeeded"
        assert finished.json()["stop_reason"] == "run.finish"
        assert replayed.status_code == 200
        assert invalid.status_code == 400


def test_finish_rejects_queued_run() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "lab"}
        ).json()
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "web",
                "spec": {},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "start:1",
            },
        ).json()

        response = client.post(
            f"/api/v1/runs/{run['run_id']}/finish",
            json={
                "outcome": "succeeded",
                "idempotency_key": "finish:1",
            },
        )

        assert response.status_code == 400
