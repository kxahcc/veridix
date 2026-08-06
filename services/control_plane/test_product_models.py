from __future__ import annotations

from services.control_plane.app.asset_store import AssetStore
from services.control_plane.app.main import create_app
from services.control_plane.app.registry import RuntimeRegistry
from services.control_plane.app.session_store import SessionStore
from services.control_plane.app.risk_service import risk_score_for, summarize
from services.evidence_service.evidence_store import EvidenceStore
from services.evidence_service.models import Finding, FindingStatus
from fastapi.testclient import TestClient


def test_asset_store_upsert_list_update_delete() -> None:
    store = AssetStore(":memory:")
    first = store.upsert(
        project_id="p1",
        kind="url",
        value="https://lab.example.test",
        source="target",
    )
    second = store.upsert(
        project_id="p1",
        kind="url",
        value="https://lab.example.test",
        source="target",
    )
    assert first["asset_id"] == second["asset_id"]
    rows = store.list(project_id="p1")
    assert len(rows) == 1
    updated = store.update(first["asset_id"], status="retired")
    assert updated["status"] == "retired"
    assert store.delete(first["asset_id"]) is True
    assert store.list() == []


def test_session_store_upsert_touch_update_delete() -> None:
    store = SessionStore(":memory:")
    session = store.upsert_for_run(
        run_id="r1",
        project_id="p1",
        title="web discovery",
    )
    assert session["archived"] is False
    touched = store.touch("r1", last_message="继续枚举 /admin")
    assert touched["last_message"] == "继续枚举 /admin"
    renamed = store.update(session["session_id"], title="renamed", archived=True)
    assert renamed["title"] == "renamed"
    assert renamed["archived"] is True
    assert len(store.list()) == 0
    assert len(store.list(archived=True)) == 1
    assert store.delete(session["session_id"]) is True


def test_evidence_finding_metadata_columns() -> None:
    store = EvidenceStore(":memory:")
    finding = Finding(
        finding_id="f1",
        run_id="r1",
        target_ref="https://lab.example.test",
        vuln_category="SQLi",
        endpoint="https://lab.example.test/login",
        status=FindingStatus.VERIFIED,
    )
    store.save_finding(finding)
    updated = store.update_finding_metadata(
        "f1",
        severity="critical",
        remediation="参数化查询",
        asset_id="asset_1",
    )
    assert updated.severity == "critical"
    assert updated.remediation == "参数化查询"
    assert updated.asset_id == "asset_1"


def test_risk_summary_counts_and_score() -> None:
    findings = [
        Finding(
            finding_id="f1",
            target_ref="t",
            vuln_category="XSS",
            endpoint="https://x.test/a",
            status=FindingStatus.VERIFIED,
            severity="high",
        ),
        Finding(
            finding_id="f2",
            target_ref="t",
            vuln_category="SQLi",
            endpoint="https://x.test/b",
            status=FindingStatus.REJECTED,
            severity="critical",
        ),
    ]
    summary = summarize(findings)
    assert summary["total_findings"] == 2
    assert summary["open_count"] == 1
    assert summary["severity_counts"]["high"] == 1
    assert summary["severity_counts"]["critical"] == 1
    assert summary["risk_score"] == risk_score_for("high", "verified") + risk_score_for(
        "critical", "rejected"
    )


def test_assets_sessions_and_vulns_api_round_trip() -> None:
    with TestClient(create_app(":memory:")) as client:
        project = client.post(
            "/api/v1/projects",
            json={"name": "api-models"},
        ).json()
        target = client.post(
            f"/api/v1/projects/{project['project_id']}/targets",
            json={"url": "https://api.example.test"},
        ).json()
        assets = client.get("/api/v1/assets").json()
        assert any(
            asset["value"] == "https://api.example.test"
            for asset in assets
        )
        mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project["project_id"],
                "name": "api models run",
                "spec": {"target_id": target["target_id"], "mission": "verify"},
            },
        ).json()
        run = client.post(
            f"/api/v1/missions/{mission['mission_id']}/runs",
            json={
                "mission_id": mission["mission_id"],
                "idempotency_key": "api-models-run-1",
            },
        ).json()
        sessions = client.get("/api/v1/sessions").json()
        assert len(sessions) == 1
        assert sessions[0]["run_id"] == run["run_id"]
        client.post(
            f"/api/v1/runs/{run['run_id']}/web-observations",
            json={
                "observations": [
                    {
                        "request_id": "req-1",
                        "web_session_id": "ws-1",
                        "proxy_session_id": "p-1",
                        "method": "GET",
                        "url": "https://api.example.test/admin",
                        "endpoint": "https://api.example.test/admin",
                        "status_code": 200,
                        "request_headers": {},
                        "response_headers": {},
                        "request_body": "",
                        "response_body": "",
                        "content_type": "text/html",
                        "request_size": 0,
                        "response_size": 0,
                        "artifact_ref": "",
                        "redacted": False,
                        "truncated": False,
                    }
                ]
            },
        )
        assets = client.get("/api/v1/assets").json()
        assert any(asset["kind"] == "host" for asset in assets)
        finding = client.post(
            f"/api/v1/runs/{run['run_id']}/findings",
            json={
                "target_ref": "https://api.example.test",
                "vuln_category": "XSS",
                "endpoint": "https://api.example.test/admin",
            },
        ).json()
        assert finding["asset_id"], "finding should auto-link to an asset"
        risk = client.get("/api/v1/risk").json()
        assert risk["total_findings"] >= 1
        templates = client.get("/api/v1/runtime/role-templates").json()
        assert any(item["template_id"] == "scanner_verify" for item in templates)
        assert any(
            item["template_id"] == "redteam_orchestration"
            for item in templates
        )


def test_provider_registry_config_roundtrip() -> None:
    registry = RuntimeRegistry(":memory:")
    registry.upsert_provider(
        "openai",
        "gpt-4o",
        "https://api.openai.com/v1",
        "ok",
        config={
            "timeout_seconds": 9,
            "thinking_mode": "high",
            "headers": {"X-Test": "1"},
        },
    )
    rows = registry.list("providers")
    assert rows[0]["config"]["timeout_seconds"] == 9
    assert rows[0]["config"]["thinking_mode"] == "high"
    assert registry.delete_provider("openai") is True


def test_provider_presets_and_retrieval_settings_api() -> None:
    with TestClient(create_app(":memory:")) as client:
        presets = client.get("/api/v1/providers/presets").json()
        assert any(item["id"] == "ollama" for item in presets)
        assert any(item["id"] == "openai" for item in presets)
        client.post(
            "/api/v1/settings/retrieval",
            json={
                "embedding": {"backend": "ollama", "model": "bge-m3"},
                "vector_store": {"type": "qdrant"},
                "graph": {"backend": "neo4j"},
                "rerank": {"enabled": True, "model": "bge-reranker-v2-m3"},
            },
        )
        saved = client.get("/api/v1/settings/retrieval").json()
        assert saved["embedding"]["backend"] == "ollama"
        assert saved["vector_store"]["type"] == "qdrant"
