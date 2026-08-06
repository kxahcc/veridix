from __future__ import annotations

import sys
import time

import pytest

from runners.container.pty_session import WindowsConPTYBackend


@pytest.mark.integration
def test_windows_conpty_spawn_read_write_close() -> None:
    if sys.platform != "win32":
        pytest.skip("ConPTY backend is Windows-only")
    pytest.importorskip("winpty")

    backend = WindowsConPTYBackend()
    backend.spawn(["cmd.exe", "/c", "echo conpty-ok"])

    output = ""
    deadline = time.time() + 10.0
    while "conpty-ok" not in output and time.time() < deadline:
        output += backend.read(timeout=0.5)

    assert "conpty-ok" in output
    backend.close()
