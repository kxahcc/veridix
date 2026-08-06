from __future__ import annotations

import socket
import threading
import time

import paramiko
import pytest
from fastapi.testclient import TestClient

from runners.remote.artifact_transfer import (
    ArtifactTransferServer,
    sign_artifact,
)
from runners.remote.models import NodeRegistration
from runners.remote.registry import RemoteNodeRegistry
from runners.remote.signing import generate_keypair
from runners.remote.ssh_backend import SshBackend
from services.control_plane.app.artifact_store import ArtifactStore


ARTIFACT_BYTES = b"ssh-evidence-body"


class FileSshServer(paramiko.ServerInterface):
    def check_auth_password(self, username: str, password: str):
        if username == "test" and password == "test":
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_exec_request(self, channel, command: str) -> bool:
        if command == b"cat evidence":
            channel.send(ARTIFACT_BYTES)
        channel.send_exit_status(0)
        channel.shutdown_write()
        return True


@pytest.mark.integration
def test_signed_artifact_return_over_ssh_and_http(tmp_path) -> None:
    pytest.importorskip("paramiko")
    private_key, public_key = generate_keypair()
    host_key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        conn, _ = listener.accept()
        transport = paramiko.Transport(conn)
        transport.add_server_key(host_key)
        transport.start_server(server=FileSshServer())
        while transport.is_active():
            time.sleep(0.05)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        backend = SshBackend(
            host="127.0.0.1",
            port=port,
            username="test",
            password="test",
        ).connect()
        try:
            data = backend.run_bytes("cat evidence")
            assert data == ARTIFACT_BYTES
        finally:
            backend.close()

        registry = RemoteNodeRegistry(":memory:")
        registry.register(
            NodeRegistration(
                node_id="node_ssh",
                version="1",
                capabilities=("shell",),
                public_key=public_key,
            )
        )
        store = ArtifactStore(tmp_path / "artifacts")
        client = TestClient(
            ArtifactTransferServer(
                registry=registry,
                artifact_store=store,
            ).create_app()
        )
        payload = sign_artifact(
            data=data,
            node_id="node_ssh",
            private_key_hex=private_key,
        )

        response = client.post("/artifacts", json=payload)

        assert response.status_code == 200
        assert response.json()["accepted"] is True
        assert store.get(payload["artifact_id"]) == ARTIFACT_BYTES
    finally:
        listener.close()
        thread.join(timeout=5)
