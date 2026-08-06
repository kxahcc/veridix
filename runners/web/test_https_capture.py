from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from datetime import datetime, timedelta, timezone

from runners.web.browser_session import BrowserSessionManager
from runners.web.proxy_gateway import ProxyGateway, RequestStore

ROOT = Path(__file__).resolve().parents[2]


class HttpsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/login":
            body = "<html>token=secret-value</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "session=secret-abc; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


def _self_signed_cert(path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(_ip())]), False)
        .sign(key, hashes.SHA256())
    )
    (path / "cert.pem").write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
    )
    (path / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def _ip():
    import ipaddress

    return ipaddress.ip_address("127.0.0.1")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _chromium_executable() -> str | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    candidates = sorted(
        base.glob("chromium-*/chrome-win/chrome.exe"),
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


@pytest.mark.integration
def test_https_capture_through_mitmproxy() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="https_", dir=ROOT / ".tmp"))
    _self_signed_cert(tmpdir)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmpdir / "cert.pem", tmpdir / "key.pem")
    server = ThreadingHTTPServer(("127.0.0.1", 0), HttpsHandler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    https_url = f"https://127.0.0.1:{server.server_address[1]}"
    proxy_port = _free_port()
    gateway: ProxyGateway | None = None
    sessions = BrowserSessionManager()
    try:
        (ROOT / ".tmp").mkdir(exist_ok=True)
        out_path = tmpdir / "capture.jsonl"
        gateway = ProxyGateway(str(ROOT))
        gateway.start(
            listen_port=proxy_port,
            out_path=out_path,
            confdir=tmpdir,
        )
        import time

        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                import httpx

                httpx.get(
                    f"https://127.0.0.1:{server.server_address[1]}/login",
                    proxy=f"http://127.0.0.1:{proxy_port}",
                    verify=False,
                    timeout=1.0,
                )
                break
            except Exception:
                time.sleep(0.25)

        handle = sessions.open(
            session_id="browser_https",
            proxy_url=f"http://127.0.0.1:{proxy_port}",
            executable_path=_chromium_executable(),
            ignore_https_errors=True,
        )
        sessions.navigate(handle, f"{https_url}/login")
        sessions.close(handle)
        gateway.stop()
        gateway = None

        store = RequestStore.load(str(out_path))
        records = store.records()
        login = next(record for record in records if "/login" in record.url)
        assert login.url.startswith("https://")
        assert "secret-value" not in login.response_body
        cookie = next(
            (
                value
                for key, value in login.response_headers.items()
                if key.lower() == "set-cookie"
            ),
            None,
        )
        assert cookie == "[REDACTED:set-cookie]"
    finally:
        if gateway is not None:
            gateway.stop()
        server.shutdown()
        thread.join(timeout=5)
        shutil.rmtree(tmpdir, ignore_errors=True)
