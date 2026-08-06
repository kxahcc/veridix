from __future__ import annotations

import socket
from typing import Any


class SshCommandError(RuntimeError):
    pass


class SshBackend:
    """Real SSH command transport using the optional `paramiko` package."""

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

    def connect(self) -> "SshBackend":
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
        return self

    def run(self, command: str, timeout: int = 30) -> dict[str, Any]:
        if self._client is None:
            raise SshCommandError("ssh backend is not connected")
        transport = self._client.get_transport()
        if transport is None:
            raise SshCommandError("ssh backend has no transport")
        channel = transport.open_session()
        channel.settimeout(timeout)
        channel.exec_command(command)
        out = _read_channel(channel.recv)
        err = _read_channel(channel.recv_stderr)
        status = channel.recv_exit_status()
        channel.close()
        return {
            "exit_status": status,
            "stdout": out,
            "stderr": err,
        }

    def run_bytes(self, command: str, timeout: int = 30) -> bytes:
        if self._client is None:
            raise SshCommandError("ssh backend is not connected")
        transport = self._client.get_transport()
        if transport is None:
            raise SshCommandError("ssh backend has no transport")
        channel = transport.open_session()
        channel.settimeout(timeout)
        channel.exec_command(command)
        out = _read_channel_bytes(channel.recv)
        _read_channel_bytes(channel.recv_stderr)
        status = channel.recv_exit_status()
        channel.close()
        if status != 0:
            raise SshCommandError(
                f"remote command failed with exit status {status}"
            )
        return out

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _read_channel(recv) -> str:
    return _read_channel_bytes(recv).decode("utf-8", errors="replace")


def _read_channel_bytes(recv) -> bytes:
    parts: list[bytes] = []
    while True:
        try:
            chunk = recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        parts.append(chunk)
    return b"".join(parts)
