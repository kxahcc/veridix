from __future__ import annotations

import os
import shutil
import socket
import tempfile
import time
from pathlib import Path

from runners.web.browser_session import BrowserSessionManager
from runners.web.proxy_gateway import ProxyGateway, RequestStore

from .contracts import ExecutionRequest, ExecutionResult


class WebToolRunner:
    """Runs browser/proxy agent tools through the real web data plane."""

    def __init__(
        self,
        root_dir: str,
        *,
        executable_path: str | None = None,
    ) -> None:
        self._root = Path(root_dir)
        self._executable_path = executable_path or _chromium_executable()
        self._gateway: ProxyGateway | None = None
        self._listen_port: int | None = None
        self._sessions = BrowserSessionManager()
        temp_root = self._root / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self._tmpdir = Path(
            tempfile.mkdtemp(prefix="web_tool_", dir=temp_root)
        )
        self._out_path = self._tmpdir / "capture.jsonl"

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        arguments = request.input
        if request.tool_ref == "browser.open":
            url = str(arguments.get("url", ""))
            if not url:
                raise ValueError("browser.open requires url")
            proxy_url = self._ensure_gateway()
            handle = self._sessions.open(
                session_id=f"browser_{request.action_id}",
                proxy_url=proxy_url,
                executable_path=self._executable_path,
            )
            try:
                self._sessions.navigate(handle, url)
            finally:
                self._sessions.close(handle)
            count = len(RequestStore.load(self._out_path).records())
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                exit_code=0,
                stdout=f"captured {count} observations",
                artifact_refs=(f"artifact://{request.action_id}/capture",),
            )
        if request.tool_ref == "proxy.list":
            records = RequestStore.load(self._out_path).records()
            summary = "\n".join(
                f"{record.method} {record.url} {record.status_code}"
                for record in records
            )
            return ExecutionResult(
                action_id=request.action_id,
                status="completed",
                exit_code=0,
                stdout=summary or "no observations captured",
                artifact_refs=(f"artifact://{request.action_id}/proxy-list",),
            )
        raise ValueError(f"unsupported web tool {request.tool_ref}")

    def close(self) -> None:
        if self._gateway is not None:
            self._gateway.stop()
            self._gateway = None
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def observations(self):
        return RequestStore.load(self._out_path).records()

    def _ensure_gateway(self) -> str:
        if self._gateway is None:
            self._start_gateway()
        return f"http://127.0.0.1:{self._listen_port}"

    def _start_gateway(self) -> None:
        self._tmpdir.mkdir(parents=True, exist_ok=True)
        port = _free_port()
        self._gateway = ProxyGateway(str(self._root))
        self._gateway.start(
            listen_port=port,
            out_path=self._out_path,
            confdir=self._tmpdir,
        )
        self._listen_port = port
        try:
            _wait_port(port)
        except Exception:
            self._gateway.stop()
            self._gateway = None
            port = _free_port()
            self._gateway = ProxyGateway(str(self._root))
            self._gateway.start(
                listen_port=port,
                out_path=self._out_path,
                confdir=self._tmpdir,
            )
            self._listen_port = port
            _wait_port(port)


def _chromium_executable() -> str | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    candidates = sorted(
        base.glob("chromium-*/chrome-win/chrome.exe"),
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_port(port: int, timeout: float | None = None) -> None:
    if timeout is None:
        timeout = float(os.environ.get("VERIDIX_PROXY_WAIT_SECONDS", "30"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"proxy did not become ready on port {port}")
