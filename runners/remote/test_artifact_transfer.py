from __future__ import annotations

from fastapi.testclient import TestClient

from services.control_plane.app.artifact_store import ArtifactStore
from runners.remote.artifact_transfer import (
    ArtifactTransferServer,
    sign_artifact,
)
from runners.remote.models import NodeRegistration
from runners.remote.registry import RemoteNodeRegistry
from runners.remote.signing import generate_keypair


def test_signed_artifact_upload_verifies_and_stores(tmp_path) -> None:
    private_key, public_key = generate_keypair()
    registry = RemoteNodeRegistry(":memory:")
    registry.register(
        NodeRegistration(
            node_id="node_1",
            version="1",
            capabilities=("shell",),
            public_key=public_key,
        )
    )
    store = ArtifactStore(tmp_path / "artifacts")
    client = TestClient(
        ArtifactTransferServer(registry=registry, artifact_store=store).create_app()
    )
    payload = sign_artifact(
        data=b"evidence-body",
        node_id="node_1",
        private_key_hex=private_key,
    )

    response = client.post("/artifacts", json=payload)

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert store.get(payload["artifact_id"]) == b"evidence-body"


def test_signed_artifact_upload_rejects_tampered_signature(tmp_path) -> None:
    _, public_key = generate_keypair()
    other_private, _ = generate_keypair()
    registry = RemoteNodeRegistry(":memory:")
    registry.register(
        NodeRegistration(
            node_id="node_2",
            version="1",
            capabilities=("shell",),
            public_key=public_key,
        )
    )
    store = ArtifactStore(tmp_path / "artifacts")
    client = TestClient(
        ArtifactTransferServer(registry=registry, artifact_store=store).create_app()
    )
    payload = sign_artifact(
        data=b"evidence-body",
        node_id="node_2",
        private_key_hex=other_private,
    )

    response = client.post("/artifacts", json=payload)

    assert response.status_code == 403
    assert store.used_bytes() == 0
