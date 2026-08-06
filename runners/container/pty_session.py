from __future__ import annotations

from abc import ABC, abstractmethod
import queue
import threading
import sys
from typing import Callable

from .resource_handle import ResourceHandle, ResourceManager


class PtyBackend(ABC):
    @abstractmethod
    def spawn(self, command: list[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def read(self, timeout: float = 0.1) -> str:
        raise NotImplementedError

    @abstractmethod
    def write(self, data: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class PseudoPtyBackend(PtyBackend):
    """In-memory backend for lifecycle tests and offline development."""

    def __init__(self) -> None:
        self.command: list[str] = []
        self.output: list[str] = []
        self.input: list[str] = []
        self.closed = False

    def spawn(self, command: list[str]) -> None:
        self.command = list(command)
        self.output.append(f"$ {' '.join(command)}")

    def read(self, timeout: float = 0.1) -> str:
        if not self.output:
            return ""
        return self.output.pop(0)

    def write(self, data: str) -> None:
        self.input.append(data)

    def close(self) -> None:
        self.closed = True


class PosixPtyBackend(PtyBackend):
    """Real PTY backend for POSIX hosts using os.openpty + subprocess."""

    def __init__(self) -> None:
        import os

        self._os = os
        self._master_fd: int | None = None
        self._proc = None

    def spawn(self, command: list[str]) -> None:
        import pty
        import subprocess

        master, slave = pty.openpty()
        self._master_fd = master
        self._proc = subprocess.Popen(
            command,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
        )
        self._os.close(slave)

    def read(self, timeout: float = 0.1) -> str:
        import select

        if self._master_fd is None:
            return ""
        ready, _, _ = select.select([self._master_fd], [], [], timeout)
        if not ready:
            return ""
        data = self._os.read(self._master_fd, 4096)
        return data.decode("utf-8", errors="replace")

    def write(self, data: str) -> None:
        if self._master_fd is None:
            raise RuntimeError("pty is not spawned")
        self._os.write(self._master_fd, data.encode("utf-8"))

    def close(self) -> None:
        if self._master_fd is not None:
            self._os.close(self._master_fd)
            self._master_fd = None
        if self._proc is not None:
            self._proc.terminate()


class WindowsConPTYBackend(PtyBackend):
    """Real ConPTY backend for Windows using the optional `winpty` package."""

    def __init__(self) -> None:
        self._proc = None
        self._buffer: queue.Queue[str] = queue.Queue()
        self._reader: threading.Thread | None = None

    def spawn(self, command: list[str]) -> None:
        from winpty import PtyProcess

        self._proc = PtyProcess.spawn(list(command))
        self._reader = threading.Thread(
            target=self._read_loop,
            daemon=True,
        )
        self._reader.start()

    def _read_loop(self) -> None:
        try:
            while self._proc is not None:
                data = self._proc.read(4096)
                if not data:
                    break
                self._buffer.put(data)
        except Exception:
            pass

    def read(self, timeout: float = 0.1) -> str:
        try:
            return self._buffer.get(timeout=timeout)
        except queue.Empty:
            return ""

    def write(self, data: str) -> None:
        if self._proc is None:
            raise RuntimeError("pty is not spawned")
        self._proc.write(data)

    def close(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None


def default_backend_for_platform() -> Callable[[], PtyBackend]:
    if sys.platform == "win32":
        try:
            import winpty  # noqa: F401
        except ImportError:
            return PseudoPtyBackend
        return WindowsConPTYBackend
    if sys.platform in ("linux", "darwin"):
        return PosixPtyBackend
    return PseudoPtyBackend


class PtySessionManager:
    def __init__(
        self,
        backend_factory: Callable[[], PtyBackend] | None = None,
    ) -> None:
        self._resources = ResourceManager()
        self._backend_factory = backend_factory or PseudoPtyBackend

    def create(self, session_id: str) -> ResourceHandle:
        handle = self._resources.create(session_id, "pty")
        handle.metadata["backend"] = None
        return handle

    def spawn(
        self,
        session_id: str,
        command: list[str],
    ) -> ResourceHandle:
        handle = self._resources.get(session_id)
        backend = self._backend_factory()
        backend.spawn(command)
        handle.metadata["backend"] = backend
        return self._resources.mark_ready(session_id)

    def attach(self, session_id: str) -> ResourceHandle:
        return self._resources.attach(session_id)

    def detach(self, session_id: str) -> ResourceHandle:
        return self._resources.detach(session_id)

    def write(self, session_id: str, data: str) -> None:
        backend = self._backend(session_id, require_active=True)
        backend.write(data)

    def read(self, session_id: str, timeout: float = 0.1) -> str:
        backend = self._backend(session_id, require_active=True)
        return backend.read(timeout=timeout)

    def close(self, session_id: str) -> ResourceHandle:
        handle = self._resources.get(session_id)
        backend = handle.metadata.get("backend")
        if backend is not None:
            backend.close()
        return self._resources.close(session_id)

    def _backend(
        self,
        session_id: str,
        *,
        require_active: bool,
    ) -> PtyBackend:
        handle = self._resources.get(session_id)
        if require_active and handle.status.value != "active":
            raise RuntimeError(
                f"pty {session_id} is not active (status={handle.status.value})"
            )
        backend = handle.metadata.get("backend")
        if backend is None:
            raise RuntimeError(f"pty {session_id} has no backend")
        return backend
