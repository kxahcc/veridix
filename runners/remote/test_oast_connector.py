from __future__ import annotations

from fastapi.testclient import TestClient

from runners.remote.oast import OastStore
from runners.remote.oast_connector import OastConnector


def test_oast_connector_redeems_one_time_token() -> None:
    store = OastStore(":memory:")
    client = TestClient(OastConnector(store).create_app())
    token = store.issue_token(source="http", purpose="canary")

    first = client.post(
        f"/callback/{token.token}",
        json={"path": "/canary"},
    )
    assert first.status_code == 200
    assert first.json()["accepted"] is True
    assert len(store.find(token.token)) == 1

    second = client.post(f"/callback/{token.token}", json={})
    assert second.status_code == 404


def test_oast_connector_rejects_unknown_token() -> None:
    store = OastStore(":memory:")
    client = TestClient(OastConnector(store).create_app())

    response = client.post("/callback/oast_unknown", json={})

    assert response.status_code == 404
