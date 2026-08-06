from __future__ import annotations

import json

from fastapi.testclient import TestClient

from services.control_plane.app.main import create_app


def test_default_local_identity_is_admin() -> None:
    with TestClient(create_app(":memory:")) as client:
        response = client.get("/api/v1/runtime/providers")
        assert response.status_code == 200


def test_global_token_is_required_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("VERIDIX_CONTROL_TOKEN", "secret-token")
    with TestClient(create_app(":memory:")) as client:
        denied = client.get("/api/v1/runtime/providers")
        assert denied.status_code == 401
        allowed = client.get(
            "/api/v1/runtime/providers",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert allowed.status_code == 200


def test_user_map_derives_role_from_token(monkeypatch) -> None:
    monkeypatch.setenv(
        "VERIDIX_CONTROL_USERS",
        '{"op-token": {"role": "operator"}, "admin-token": {"role": "admin"}}',
    )
    with TestClient(create_app(":memory:")) as client:
        denied = client.get(
            "/api/v1/runtime/providers",
            headers={"Authorization": "Bearer bad-token"},
        )
        assert denied.status_code == 401
        # The client role header must not be trusted when user map is active.
        spoofed = client.get(
            "/api/v1/runtime/providers",
            headers={
                "Authorization": "Bearer op-token",
                "X-Veridix-Role": "admin",
            },
        )
        assert spoofed.status_code == 200


def test_login_and_project_scope(monkeypatch) -> None:
    monkeypatch.setenv(
        "VERIDIX_CONTROL_USERS",
        (
            '{"admin-token": {"role": "admin"}}'
        ),
    )
    with TestClient(create_app(":memory:")) as client:
        project_a = client.post(
            "/api/v1/projects",
            json={"name": "A"},
            headers={"Authorization": "Bearer admin-token"},
        ).json()
        client.post(
            "/api/v1/projects",
            json={"name": "B"},
            headers={"Authorization": "Bearer admin-token"},
        )
        monkeypatch.setenv(
            "VERIDIX_CONTROL_USERS",
            json.dumps(
                {
                    "op-token": {
                        "role": "operator",
                        "projects": [project_a["project_id"]],
                    }
                }
            ),
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"token": "op-token"},
        )
        assert login.status_code == 200
        assert login.json()["role"] == "operator"
        rows = client.get(
            "/api/v1/projects",
            headers={"Authorization": "Bearer op-token"},
        ).json()
        assert [row["project_id"] for row in rows] == [project_a["project_id"]]


def test_operator_write_is_scoped_to_assigned_projects(monkeypatch) -> None:
    monkeypatch.setenv(
        "VERIDIX_CONTROL_USERS",
        '{"admin-token": {"role": "admin"}}',
    )
    with TestClient(create_app(":memory:")) as client:
        project_a = client.post(
            "/api/v1/projects",
            json={"name": "A"},
            headers={"Authorization": "Bearer admin-token"},
        ).json()
        project_b = client.post(
            "/api/v1/projects",
            json={"name": "B"},
            headers={"Authorization": "Bearer admin-token"},
        ).json()
        monkeypatch.setenv(
            "VERIDIX_CONTROL_USERS",
            json.dumps(
                {
                    "op-token": {
                        "role": "operator",
                        "projects": [project_a["project_id"]],
                    }
                }
            ),
        )
        op_headers = {"Authorization": "Bearer op-token"}

        denied_mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project_b["project_id"],
                "name": "denied",
                "spec": {},
            },
            headers=op_headers,
        )
        assert denied_mission.status_code == 403

        allowed_mission = client.post(
            "/api/v1/missions",
            json={
                "project_id": project_a["project_id"],
                "name": "allowed",
                "spec": {},
            },
            headers=op_headers,
        )
        assert allowed_mission.status_code == 200

        denied_asset = client.post(
            "/api/v1/assets",
            json={
                "project_id": project_b["project_id"],
                "kind": "url",
                "value": "https://b.example.test",
                "source": "test",
            },
            headers=op_headers,
        )
        assert denied_asset.status_code == 403

        allowed_read = client.get(
            f"/api/v1/missions/{allowed_mission.json()['mission_id']}",
            headers=op_headers,
        )
        assert allowed_read.status_code == 200

        denied_delete = client.delete(
            f"/api/v1/projects/{project_b['project_id']}",
            headers=op_headers,
        )
        assert denied_delete.status_code == 403
