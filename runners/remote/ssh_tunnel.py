from __future__ import annotations

import socket
import threading
from typing import Any

from .transports import TransportSpec


class SshTunnelError(RuntimeError):
    pass


class SshTunnel:
    """Local port forward through an SSH direct-tcpip channel."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 22,
        username: str,
        password: str | None = None,
        key_path: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._key_path = key_path
        self._client = None
        self._listener: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    @property
    def local_address(self) -> tuple[str, int] | None:
        if self._listener is None:
            return None
        return self._listener.getsockname()

    def start(
        self,
        *,
        remote_host: str,
        remote_port: int,
        local_bind_host: str = "127.0.0.1",
        local_bind_port: int = 0,
    ) -> "SshTunnel":
        import paramiko

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            key_filename=self._key_path,
            allow_agent=False,
            look_for_keys=False,
        )
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((local_bind_host, local_bind_port))
        listener.listen(16)
        self._listener = listener
        thread = threading.Thread(
            target=self._accept_loop,
            args=(remote_host, remote_port),
            daemon=True,
        )
        thread.start()
        self._threads.append(thread)
        return self

    def _accept_loop(self, remote_host: str, remote_port: int) -> None:
        while not self._stop.is_set():
            try:
                conn, origin = self._listener.accept()
            except OSError:
                break
            try:
                channel = self._client.get_transport().open_channel(
                    "direct-tcpip",
                    (remote_host, remote_port),
                    origin,
                )
            except Exception:
                conn.close()
                continue
            thread = threading.Thread(
                target=_relay,
                args=(conn, channel),
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def close(self) -> None:
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        if self._client is not None:
            self._client.close()
            self._client = None
        for thread in self._threads:
            thread.join(timeout=2)


def connect_ssh_tunnel(
    spec: TransportSpec,
    *,
    username: str,
    password: str | None = None,
    key_path: str | None = None,
) -> SshTunnel:
    spec.validate()
    if spec.kind != "ssh_tunnel":
        raise SshTunnelError(f"expected ssh_tunnel transport, got {spec.kind}")
    tunnel_host, tunnel_port = _parse_host_port(
        spec.tunnel_ref.removeprefix("ssh://"),
        22,
    )
    remote_host, remote_port = _parse_host_port(spec.endpoint, 80)
    return SshTunnel(
        host=tunnel_host,
        port=tunnel_port,
        username=username,
        password=password,
        key_path=key_path,
    ).start(remote_host=remote_host, remote_port=remote_port)


def _parse_host_port(value: str, default_port: int) -> tuple[str, int]:
    if ":" in value:
        host, port_text = value.rsplit(":", 1)
        try:
            return host, int(port_text)
        except ValueError as error:
            raise SshTunnelError(f"invalid port in {value}") from error
    return value, default_port


def _relay(conn: socket.socket, channel: Any) -> None:
    def pump(source, destination) -> None:
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                destination.sendall(data)
        except OSError:
            pass
        finally:
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    try:
        forward = threading.Thread(
            target=pump,
            args=(conn, channel),
            daemon=True,
        )
        backward = threading.Thread(
            target=pump,
            args=(channel, conn),
            daemon=True,
        )
        forward.start()
        backward.start()
        forward.join(timeout=30)
        backward.join(timeout=30)
    finally:
        try:
            channel.close()
        except Exception:
            pass
        conn.close()
