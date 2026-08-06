from __future__ import annotations

import base64
import hashlib
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from services.control_plane.app.artifact_store import ArtifactStore

from .registry import RemoteNodeRegistry
from .signing import sign_payload, verify_payload


class ArtifactUploadIn(BaseModel):
    node_id: str
    artifact_id: str
    size: int
    signature: str
    data_b64: str


class ArtifactTransferServer:
    def __init__(
        self,
        *,
        registry: RemoteNodeRegistry,
        artifact_store: ArtifactStore,
    ) -> None:
        self._registry = registry
        self._artifacts = artifact_store

    def create_app(self) -> FastAPI:
        application = FastAPI(
            title="veridix signed artifact transfer",
            version="0.1.0",
        )

        @application.get("/healthz")
        def healthz() -> dict:
            return {"status": "ok", "service": "artifact-transfer"}

        @application.post("/artifacts")
        def upload(body: ArtifactUploadIn) -> dict:
            node = self._registry.get(body.node_id)
            data = base64.b64decode(body.data_b64)
            digest = hashlib.sha256(data).hexdigest()
            if digest != body.artifact_id:
                raise HTTPException(status_code=400, detail="artifact hash mismatch")
            manifest = _artifact_manifest(
                node_id=body.node_id,
                artifact_id=body.artifact_id,
                size=len(data),
            )
            if not verify_payload(manifest, body.signature, node.public_key):
                raise HTTPException(status_code=403, detail="artifact signature invalid")
            self._artifacts.put(data, content_type="application/octet-stream")
            return {"accepted": True, "artifact_id": body.artifact_id}

        return application


def sign_artifact(
    *,
    data: bytes,
    node_id: str,
    private_key_hex: str,
) -> dict[str, Any]:
    artifact_id = hashlib.sha256(data).hexdigest()
    manifest = _artifact_manifest(
        node_id=node_id,
        artifact_id=artifact_id,
        size=len(data),
    )
    return {
        "node_id": node_id,
        "artifact_id": artifact_id,
        "size": len(data),
        "signature": sign_payload(manifest, private_key_hex),
        "data_b64": base64.b64encode(data).decode("ascii"),
    }


def _artifact_manifest(*, node_id: str, artifact_id: str, size: int) -> dict:
    return {
        "node_id": node_id,
        "artifact_id": artifact_id,
        "size": size,
    }
