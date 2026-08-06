from __future__ import annotations

import pytest

from runners.container.pty_session import (
    PosixPtyBackend,
    PseudoPtyBackend,
    PtySessionManager,
)


def test_pty_session_attach_detach_reconnect_and_io() -> None:
    manager = PtySessionManager()
    manager.create("pty_1")
    manager.spawn("pty_1", ["bash", "-c", "echo hi"])

    manager.attach("pty_1")
    assert manager.read("pty_1") == "$ bash -c echo hi"
    manager.write("pty_1", "whoami\n")
    assert manager._resources.get("pty_1").metadata["backend"].input == [
        "whoami\n"
    ]

    manager.detach("pty_1")
    with pytest.raises(RuntimeError, match="not active"):
        manager.write("pty_1", "x")

    manager.attach("pty_1")
    assert manager.read("pty_1") == ""
    manager.close("pty_1")
    assert manager._resources.get("pty_1").status.value == "closed"


def test_pty_spawn_requires_existing_session() -> None:
    manager = PtySessionManager()
    with pytest.raises(KeyError):
        manager.spawn("missing", ["sh"])


def test_posix_backend_is_constructible() -> None:
    backend = PosixPtyBackend()
    assert backend._master_fd is None
