from __future__ import annotations

import socket
import threading
import time

import paramiko
import pytest

from runners.remote.ssh_tunnel import (
    SshTunnel,
    SshTunnelError,
    connect_ssh_tunnel,
)
from runners.remote.transports import TransportSpec


class TunnelSshServer(paramiko.ServerInterface):
    def check_auth_password(self, username: str, password: str):
        if username == "test" and password == "test":
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_direct_tcpip_request(
        self,
        chanid: int,
        origin,
        destination,
    ):
        return paramiko.OPEN_SUCCEEDED


def _echo_server() -> tuple[socket.socket, int]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve() -> None:
        conn, _ = server.accept()
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                conn.sendall(data)
        except OSError:
            pass
        finally:
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return server, server.getsockname()[1]


def _relay(channel, destination: socket.socket) -> None:
    def pump(source, target) -> None:
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                target.sendall(data)
        except OSError:
            pass
        finally:
            try:
                target.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    a = threading.Thread(target=pump, args=(channel, destination), daemon=True)
    b = threading.Thread(target=pump, args=(destination, channel), daemon=True)
    a.start()
    b.start()
    a.join(timeout=10)
    b.join(timeout=10)
    try:
        channel.close()
    except Exception:
        pass
    destination.close()


@pytest.mark.integration
def test_ssh_tunnel_forwards_local_port_to_remote_service() -> None:
    pytest.importorskip("paramiko")
    echo, echo_port = _echo_server()
    host_key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    ssh_port = listener.getsockname()[1]

    def serve_ssh() -> None:
        conn, _ = listener.accept()
        transport = paramiko.Transport(conn)
        transport.add_server_key(host_key)
        transport.start_server(server=TunnelSshServer())
        while transport.is_active():
            channel = transport.accept(timeout=1)
            if channel is None:
                continue
            try:
                destination = socket.create_connection(
                    ("127.0.0.1", echo_port),
                    timeout=5,
                )
            except OSError:
                channel.close()
                continue
            _relay(channel, destination)

    ssh_thread = threading.Thread(target=serve_ssh, daemon=True)
    ssh_thread.start()
    try:
        tunnel = SshTunnel(
            host="127.0.0.1",
            port=ssh_port,
            username="test",
            password="test",
        ).start(remote_host="127.0.0.1", remote_port=echo_port)
        local_host, local_port = tunnel.local_address

        client = socket.create_connection((local_host, local_port), timeout=10)
        client.sendall(b"tunnel-ping")
        client.shutdown(socket.SHUT_WR)
        data = client.recv(4096)
        client.close()

        assert data == b"tunnel-ping"
        tunnel.close()
    finally:
        listener.close()
        echo.close()
        ssh_thread.join(timeout=5)


def test_connect_ssh_tunnel_rejects_non_tunnel_spec() -> None:
    spec = TransportSpec(kind="direct", endpoint="127.0.0.1:80")

    with pytest.raises(SshTunnelError, match="expected ssh_tunnel"):
        connect_ssh_tunnel(spec, username="test")
