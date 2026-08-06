from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from services.control_plane.app.redactor import Redactor

from .models import WebObservation
from .normalizer import normalize_endpoint, normalize_ws_channel
from .recovery import RecoveryLog, RecoveryRecord


class RequestStore:
    def __init__(self) -> None:
        self._records: list[WebObservation] = []

    def append(self, record: WebObservation) -> None:
        self._records.append(record)

    def records(self) -> tuple[WebObservation, ...]:
        return tuple(self._records)

    def by_id(self, request_id: str) -> WebObservation | None:
        return next(
            (record for record in self._records if record.request_id == request_id),
            None,
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for record in self._records:
                fh.write(json.dumps(record.to_dict(), ensure_ascii=True) + "\n")

    @classmethod
    def load(cls, path: str) -> RequestStore:
        store = cls()
        if not Path(path).exists():
            return store
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    store.append(WebObservation.from_dict(json.loads(line)))
        return store


class ProxyCaptureAddon:
    def __init__(
        self,
        out_path: str,
        *,
        max_response_bytes: int = 512 * 1024,
        web_session_id: str = "web_session_wp06",
        proxy_session_id: str = "proxy_session_wp06",
    ) -> None:
        self._out_path = out_path
        self._max_response_bytes = max_response_bytes
        self._web_session_id = web_session_id
        self._proxy_session_id = proxy_session_id
        self._store = RequestStore()
        self._redactor = Redactor()
        self._seq = 0

    def response(self, flow) -> None:
        request = flow.request
        response = flow.response
        self._seq += 1
        request_id = f"req_{self._seq:06d}"
        raw = WebObservation(
            request_id=request_id,
            web_session_id=self._web_session_id,
            proxy_session_id=self._proxy_session_id,
            method=request.method,
            url=request.pretty_url,
            endpoint=normalize_endpoint(request.method, request.pretty_url),
            status_code=response.status_code,
            request_headers=dict(request.headers),
            response_headers=dict(response.headers),
            request_body=_decode(request.raw_content),
            response_body=_decode(response.raw_content),
            content_type=response.headers.get("content-type", ""),
            request_size=len(request.raw_content or b""),
            response_size=len(response.raw_content or b""),
            artifact_ref=f"artifact://capture/{request_id}/raw",
        )
        if len(raw.response_body.encode("utf-8")) > self._max_response_bytes:
            raw = _truncate(raw, self._max_response_bytes)
        redacted = _redact(self._redactor, raw)
        self._store.append(redacted)
        with open(self._out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(redacted.to_dict(), ensure_ascii=True) + "\n")

    def websocket_message(self, flow) -> None:
        request = flow.request
        for message in flow.websocket.messages:
            self._seq += 1
            request_id = f"ws_{self._seq:06d}"
            raw = WebObservation(
                request_id=request_id,
                web_session_id=self._web_session_id,
                proxy_session_id=self._proxy_session_id,
                method="WS",
                url=request.pretty_url,
                endpoint=normalize_ws_channel(request.pretty_url),
                status_code=101,
                request_headers=dict(request.headers),
                response_headers={},
                content_type="application/websocket",
                request_size=len(message.content or b""),
                response_size=0,
                protocol="websocket",
                ws_frame_type=str(getattr(message, "type", "text")),
                ws_frame_data=_decode(message.content),
            )
            redacted = _redact(self._redactor, raw)
            self._store.append(redacted)
            with open(self._out_path, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        redacted.to_dict(),
                        ensure_ascii=True,
                    )
                    + "\n"
                )


class ProxyGateway:
    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir)
        self._proc: subprocess.Popen | None = None
        self._listen_port: int | None = None

    def start(
        self,
        *,
        listen_port: int,
        out_path: str | Path,
        confdir: str | Path,
        web_session_id: str = "web_session_wp06",
        proxy_session_id: str = "proxy_session_wp06",
        max_response_bytes: int = 512 * 1024,
    ) -> None:
        env = dict(os.environ)
        env["VERIDIX_CAPTURE_OUT"] = str(out_path)
        env["VERIDIX_CAPTURE_MAX_BYTES"] = str(max_response_bytes)
        env["VERIDIX_WEB_SESSION_ID"] = web_session_id
        env["VERIDIX_PROXY_SESSION_ID"] = proxy_session_id
        command = _mitmdump_command()
        command += [
            "-q",
            "-s",
            str(Path(__file__).parent / "mitm_addon.py"),
            "--ssl-insecure",
            "--listen-host",
            "127.0.0.1",
            "--listen-port",
            str(listen_port),
            "--set",
            f"confdir={confdir}",
        ]
        self._proc = subprocess.Popen(
            command,
            cwd=str(self._root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._listen_port = listen_port


    def wait_ready(self, base_url: str, timeout: float = 15.0) -> None:
        if self._proc is None:
            raise RuntimeError("proxy gateway is not running")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                response = httpx.get(
                    f"{base_url}/health",
                    proxy=f"http://127.0.0.1:{self._listen_port}",
                    timeout=1.0,
                )
                if response.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise RuntimeError("proxy gateway did not become ready")

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
            self._proc = None

    def restart(
        self,
        *,
        listen_port: int,
        out_path: str | Path,
        confdir: str | Path,
        web_session_id: str = "web_session_wp06",
        proxy_session_id: str = "proxy_session_wp06",
        max_response_bytes: int = 512 * 1024,
        log: RecoveryLog | None = None,
        run_id: str | None = None,
    ) -> None:
        self.stop()
        self.start(
            listen_port=listen_port,
            out_path=out_path,
            confdir=confdir,
            web_session_id=web_session_id,
            proxy_session_id=proxy_session_id,
            max_response_bytes=max_response_bytes,
        )
        if log is not None:
            log.append(
                RecoveryRecord(
                    resource_id=proxy_session_id,
                    resource_type="proxy",
                    action="restart",
                    reason="proxy_restarted",
                    from_status="stopped",
                    new_resource_id=proxy_session_id,
                    reobserve_required=True,
                    run_id=run_id,
                )
            )


def _mitmdump_command() -> list[str]:
    executable = "mitmdump.exe" if os.name == "nt" else "mitmdump"
    candidates = [
        Path(sys.executable).parent / "Scripts" / executable,
        Path(sys.executable).with_name(executable),
        Path(shutil.which(executable) or ""),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return [str(candidate)]
    return [sys.executable, "-m", "mitmproxy.tools.main"]


def _truncate(record: WebObservation, max_bytes: int) -> WebObservation:
    return WebObservation(
        request_id=record.request_id,
        web_session_id=record.web_session_id,
        proxy_session_id=record.proxy_session_id,
        method=record.method,
        url=record.url,
        endpoint=record.endpoint,
        status_code=record.status_code,
        request_headers=record.request_headers,
        response_headers=record.response_headers,
        request_body=record.request_body,
        response_body=record.response_body[:max_bytes],
        content_type=record.content_type,
        request_size=record.request_size,
        response_size=record.response_size,
        artifact_ref=record.artifact_ref,
        truncated=True,
        protocol=record.protocol,
        graphql_operation=record.graphql_operation,
        graphql_query=record.graphql_query,
        graphql_variables=record.graphql_variables,
        ws_frame_type=record.ws_frame_type,
        ws_frame_data=record.ws_frame_data,
    )


def _redact(redactor: Redactor, record: WebObservation) -> WebObservation:
    request_headers = redactor.redact_headers(record.request_headers)
    response_headers = redactor.redact_headers(record.response_headers)
    request_body = redactor.redact_text(record.request_body)
    response_body = redactor.redact_text(record.response_body)
    changed = (
        request_headers != record.request_headers
        or response_headers != record.response_headers
        or request_body != record.request_body
        or response_body != record.response_body
    )
    return WebObservation(
        request_id=record.request_id,
        web_session_id=record.web_session_id,
        proxy_session_id=record.proxy_session_id,
        method=record.method,
        url=record.url,
        endpoint=record.endpoint,
        status_code=record.status_code,
        request_headers=request_headers,
        response_headers=response_headers,
        request_body=request_body,
        response_body=response_body,
        content_type=record.content_type,
        request_size=record.request_size,
        response_size=record.response_size,
        artifact_ref=record.artifact_ref,
        redacted=record.redacted or changed,
        truncated=record.truncated,
        protocol=record.protocol,
        graphql_operation=record.graphql_operation,
        graphql_query=record.graphql_query,
        graphql_variables=record.graphql_variables,
        ws_frame_type=record.ws_frame_type,
        ws_frame_data=record.ws_frame_data,
    )


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")
