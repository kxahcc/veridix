#!/usr/bin/env python
"""Bring up the full Veridix local environment with one command.

This is the target developer/deploy flow:
  1. `python scripts/env_up.py` uses the unified
     `deploy/system/docker-compose.yml` to start storage
     (pgvector/Qdrant/Chroma/Neo4j) plus the veridix-tools environment.
  2. `python scripts/dev_stack.py` starts the host control plane, agent
     worker, lab provider and Web dev server.
  Lab targets stay separate because they are only used for testing.

Usage:
  python scripts/env_up.py
  python scripts/env_up.py --smoke --no-stack
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "storage" / "docker-compose.yml"
SYSTEM_COMPOSE = ROOT / "deploy" / "system" / "docker-compose.yml"
WAIT_TARGETS = (
    ("qdrant", "http://127.0.0.1:6333"),
    ("neo4j", "http://127.0.0.1:7474"),
    ("chroma", "http://127.0.0.1:8001/api/v2/version"),
)


def _wait_http(name: str, url: str, timeout: float = 180.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            parsed = urllib.parse.urlsplit(url)
            connection = http.client.HTTPConnection(
                parsed.hostname,
                parsed.port,
                timeout=3.0,
            )
            request_path = parsed.path or "/"
            if parsed.query:
                request_path = f"{request_path}?{parsed.query}"
            connection.request("GET", request_path)
            response = connection.getresponse()
            response.read()
            connection.close()
            if response.status < 500:
                print(f"{name}: ready", flush=True)
                return
        except Exception:
            pass
        time.sleep(2.0)
    raise RuntimeError(f"{name} did not become ready: {url}")


def _wait_port(name: str, port: int, timeout: float = 180.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3.0):
                print(f"{name}: ready", flush=True)
                return
        except OSError:
            time.sleep(2.0)
    raise RuntimeError(f"{name} port {port} did not become ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build",
        action="store_true",
        help="rebuild veridix-tools images before up",
    )
    parser.add_argument(
        "--base-image",
        default="debian:bookworm-slim",
        help="base image for veridix-tools builds",
    )
    parser.add_argument(
        "--down",
        action="store_true",
        help="tear down the system compose stack",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="run storage and tool-environment smoke after up",
    )
    parser.add_argument(
        "--no-stack",
        action="store_true",
        help="do not start the host dev stack",
    )
    parser.add_argument(
        "--system",
        action="store_true",
        help="[compat] unified system compose is now the default",
    )
    parser.add_argument(
        "--storage-only",
        action="store_true",
        help="start only storage services from deploy/storage/docker-compose.yml",
    )
    args = parser.parse_args()
    compose_file = SYSTEM_COMPOSE if not args.storage_only else COMPOSE
    profile_args = [] if args.storage_only else ["--profile", "tool-env"]

    if args.down:
        compose_env = {
            **os.environ,
            "VERIDIX_PGVECTOR_PORT": "55432",
            "VERIDIX_STORAGE_REGISTRY": "docker.m.daocloud.io/",
        }
        if not args.storage_only:
            legacy_down = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(COMPOSE),
                    "down",
                ],
                cwd=str(ROOT),
                env=compose_env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            print(legacy_down.stdout[-500:], flush=True)
        down = subprocess.run(
            [
                "docker",
                "compose",
                *profile_args,
                "-f",
                str(compose_file),
                "down",
            ],
            cwd=str(ROOT),
            env=compose_env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        print(down.stdout[-1000:], flush=True)
        return down.returncode

    if args.build:
        build = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_veridix_tools.py"),
                "--base-image",
                args.base_image,
                "--full",
                "--code-lite",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=2400,
        )
        print(build.stdout[-1200:], flush=True)
        if build.returncode != 0:
            print(build.stderr[-2000:], flush=True)
            return 1

    if not args.storage_only:
        legacy_down = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE),
                "down",
            ],
            cwd=str(ROOT),
            env={
                **os.environ,
                "VERIDIX_PGVECTOR_PORT": "55432",
                "VERIDIX_STORAGE_REGISTRY": "docker.m.daocloud.io/",
            },
            capture_output=True,
            text=True,
            timeout=300,
        )
        print(legacy_down.stdout[-500:], flush=True)

    cmd = [
        "docker",
        "compose",
        *profile_args,
        "-f",
        str(compose_file),
        "up",
        "-d",
    ]
    if args.build:
        cmd.append("--build")
    compose_env = {
        **os.environ,
        "VERIDIX_PGVECTOR_PORT": "55432",
        "VERIDIX_STORAGE_REGISTRY": "docker.m.daocloud.io/",
    }
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=compose_env,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    print(result.stdout[-1000:], flush=True)
    if result.returncode != 0:
        print(result.stderr[-2000:], flush=True)
        return 1

    for name, url in WAIT_TARGETS:
        _wait_http(name, url)
    _wait_port("pgvector", 55432)
    if not args.storage_only:
        _wait_http(
            "zap",
            (
                "http://127.0.0.1:8090/JSON/core/view/version/"
                f"?apikey={os.environ.get('VERIDIX_ZAP_API_KEY', 'veridix-zap')}"
            ),
            timeout=180.0,
        )

    if args.smoke:
        env = {
            **os.environ,
            "VERIDIX_RUNNER": "docker",
            "VERIDIX_PGVECTOR_URL": (
                "postgresql://veridix:veridix@127.0.0.1:55432/veridix"
            ),
            "VERIDIX_QDRANT_URL": "http://127.0.0.1:6333",
            "VERIDIX_CHROMA_URL": "http://127.0.0.1:8001",
            "VERIDIX_NEO4J_URI": "bolt://127.0.0.1:7687",
            "VERIDIX_NEO4J_USER": "neo4j",
            "VERIDIX_NEO4J_PASSWORD": "veridixpass",
        }
        storage = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "storage_real_smoke.py")],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        print(storage.stdout[-1500:], flush=True)
        if storage.returncode != 0:
            print(storage.stderr[-2000:], flush=True)
            return storage.returncode
        tools = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "tool_env_up.py")],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        print(tools.stdout[-1500:], flush=True)
        if tools.returncode != 0:
            print(tools.stderr[-2000:], flush=True)
            return tools.returncode

    if args.no_stack:
        print("containers ready; dev stack skipped", flush=True)
        return 0

    env = {**os.environ, "VERIDIX_RUNNER": "docker"}
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "dev_stack.py")],
        cwd=str(ROOT),
        env=env,
        timeout=120,
        check=False,
    )

    web_url = f"http://127.0.0.1:{os.environ.get('VERIDIX_WEB_PORT', '5173')}"
    control_url = f"http://127.0.0.1:{os.environ.get('VERIDIX_CONTROL_PORT', '8787')}"
    lab_url = f"http://127.0.0.1:{os.environ.get('VERIDIX_LAB_PORT', '8766')}"
    try:
        stack_info = json.loads(
            (ROOT / "runtime" / "dev-stack.json").read_text(encoding="utf-8")
        )
        web_url = stack_info.get("web_url", web_url)
        control_url = stack_info.get("control_url", control_url)
        lab_url = stack_info.get("lab_url", lab_url)
    except Exception:
        pass

    payload = {
        "compose": str(compose_file),
        "web": web_url,
        "control": control_url,
        "lab": lab_url,
        "storage": {
            "pgvector": "http://127.0.0.1:55432",
            "qdrant": "http://127.0.0.1:6333",
            "chroma": "http://127.0.0.1:8001",
            "neo4j": "http://127.0.0.1:7474",
        },
    }
    out = ROOT / "runtime" / "env.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
