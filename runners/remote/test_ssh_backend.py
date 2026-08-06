from __future__ import annotations

import socket
import threading

import paramiko
import pytest

from runners.remote.ssh_backend import SshBackend


class StubSshServer(paramiko.ServerInterface):
    def check_auth_password(self, username: str, password: str):
        if username == "test" and password == "test":
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_exec_request(self, channel, command: str) -> bool:
        channel.send(b"ssh-ok\n")
        channel.send_exit_status(0)
        channel.shutdown_write()
        return True


@pytest.mark.integration
def test_ssh_backend_runs_command_over_localhost() -> None:
    pytest.importorskip("paramiko")
    host_key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        conn, _ = listener.accept()
        transport = paramiko.Transport(conn)
        transport.add_server_key(host_key)
        transport.start_server(server=StubSshServer())
        while transport.is_active():
            import time

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
            result = backend.run("echo ok")
            assert result["exit_status"] == 0
            assert result["stdout"] == "ssh-ok\n"
        finally:
            backend.close()
    finally:
        listener.close()
        thread.join(timeout=5)
