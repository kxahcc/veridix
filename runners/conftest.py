from __future__ import annotations

import select
import socket
import sys
import time
from pathlib import Path

_ORIGINAL_SOCKETPAIR = socket.socketpair


def _safe_socketpair(*args, **kwargs):
    last_error: Exception | None = None
    for attempt in range(3):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(5.0)
            client.connect(listener.getsockname())
            ready, _, _ = select.select([listener], [], [], 5.0)
            if not ready:
                raise TimeoutError("socketpair accept timed out")
            server, _ = listener.accept()
            return server, client
        except OSError as error:
            last_error = error
            try:
                listener.close()
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass
            time.sleep(0.1 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return _ORIGINAL_SOCKETPAIR(*args, **kwargs)


if socket.socketpair is _ORIGINAL_SOCKETPAIR:
    socket.socketpair = _safe_socketpair

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
