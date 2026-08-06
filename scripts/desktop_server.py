from __future__ import annotations

import argparse
import os
import socket
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn

from services.control_plane.app.main import create_app


def serve(
    web_dir: str | Path,
    *,
    control_port: int,
    web_port: int,
):
    control, control_thread = start_control(control_port)
    web, web_thread = start_web(web_dir, web_port, control_port)
    return control, web, control_thread, web_thread


def start_control(control_port: int):
    control = uvicorn.Server(
        uvicorn.Config(
            create_app(os.environ.get("VERIDIX_CONTROL_DB", ":memory:")),
            host="127.0.0.1",
            port=control_port,
            log_level="warning",
        )
    )
    control_thread = threading.Thread(target=control.run, daemon=True)
    control_thread.start()
    return control, control_thread


def start_web(
    web_dir: str | Path,
    web_port: int,
    control_port: int,
):
    class InjectedHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(
                *args,
                directory=str(Path(web_dir)),
                **kwargs,
            )

        def _serve_index(self) -> None:
            target = Path(web_dir) / "index.html"
            body = target.read_bytes()
            script = (
                "<script>window.__VERIDIX_CONTROL_URL__ = "
                f"'http://127.0.0.1:{control_port}';</script>"
            ).encode("utf-8")
            body = body.replace(b"<head>", b"<head>" + script)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            last_segment = self.path.split("/")[-1]
            if self.path in ("/", "/index.html") or (
                "." not in last_segment
                and not self.path.startswith("/api/")
            ):
                self._serve_index()
                return
            super().do_GET()

    web = ThreadingHTTPServer(("127.0.0.1", web_port), InjectedHandler)
    web_thread = threading.Thread(target=web.serve_forever, daemon=True)
    web_thread.start()
    return web, web_thread


def main() -> int:
    parser = argparse.ArgumentParser(description="run control plane + web UI")
    parser.add_argument("--web-dir", default="apps/web/dist")
    parser.add_argument("--control-port", type=int, default=8787)
    parser.add_argument("--web-port", type=int, default=8788)
    args = parser.parse_args()

    control, web, _, _ = serve(
        args.web_dir,
        control_port=args.control_port,
        web_port=args.web_port,
    )
    print(f"control plane: http://127.0.0.1:{args.control_port}")
    print(f"web UI:       http://127.0.0.1:{args.web_port}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        control.should_exit = True
        web.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
