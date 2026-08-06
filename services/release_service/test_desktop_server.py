from __future__ import annotations

import threading
import socket
import time
from pathlib import Path

import httpx
import pytest

from scripts.desktop_server import serve


ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_http(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"endpoint did not become ready: {url}")


@pytest.mark.integration
def test_desktop_server_starts_control_and_web() -> None:
    control_port = _free_port()
    web_port = _free_port()
    control, web, control_thread, web_thread = serve(
        ROOT / "apps/web/dist",
        control_port=control_port,
        web_port=web_port,
    )
    try:
        _wait_http(f"http://127.0.0.1:{control_port}/healthz")
        _wait_http(f"http://127.0.0.1:{web_port}/index.html")
    finally:
        control.should_exit = True
        web.shutdown()
        control_thread.join(timeout=5)
        web_thread.join(timeout=5)


@pytest.mark.integration
def test_desktop_server_serves_spa_routes() -> None:
    control_port = _free_port()
    web_port = _free_port()
    control, web, control_thread, web_thread = serve(
        ROOT / "apps/web/dist",
        control_port=control_port,
        web_port=web_port,
    )
    try:
        _wait_http(f"http://127.0.0.1:{web_port}/index.html")
        response = httpx.get(f"http://127.0.0.1:{web_port}/setup")

        assert response.status_code == 200
        assert "window.__VERIDIX_CONTROL_URL__" in response.text
        assert f"127.0.0.1:{control_port}" in response.text
    finally:
        control.should_exit = True
        web.shutdown()
        control_thread.join(timeout=5)
        web_thread.join(timeout=5)
