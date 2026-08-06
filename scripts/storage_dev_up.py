#!/usr/bin/env python
"""Bring up mature storage backends locally and run the real smoke.

Requires Docker. Starts pgvector, Qdrant, Chroma and Neo4j from
deploy/storage/docker-compose.yml, waits for ports, then runs
scripts/storage_real_smoke.py against them.

Usage:
  python scripts/storage_dev_up.py            # up + smoke
  python scripts/storage_dev_up.py --down     # teardown containers
  python scripts/storage_dev_up.py --smoke-only
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "storage" / "docker-compose.yml"

PORTS = {
    "pgvector": int(os.environ.get("VERIDIX_PGVECTOR_PORT", "55432")),
    "qdrant": 6333,
    "chroma": 8001,
    "neo4j": 7687,
}


def _compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        check=True,
    )


def _wait_port(port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except OSError:
            time.sleep(2)
    return False


def _set_env() -> None:
    pg_port = str(PORTS["pgvector"])
    os.environ["VERIDIX_PGVECTOR_URL"] = (
        f"postgresql://veridix:veridix@127.0.0.1:{pg_port}/veridix"
    )
    os.environ["VERIDIX_QDRANT_URL"] = "http://127.0.0.1:6333"
    os.environ["VERIDIX_CHROMA_URL"] = "http://127.0.0.1:8001"
    os.environ["VERIDIX_NEO4J_URI"] = "bolt://127.0.0.1:7687"
    os.environ["VERIDIX_NEO4J_USER"] = "neo4j"
    os.environ["VERIDIX_NEO4J_PASSWORD"] = "veridixpass"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--down", action="store_true")
    mode.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--wait", type=float, default=120.0)
    args = parser.parse_args()

    if args.down:
        _compose("down")
        print("storage containers down")
        return 0

    if not args.smoke_only:
        try:
            _compose("up", "-d")
        except subprocess.CalledProcessError as error:
            print(
                "storage compose failed; if this is a registry/network "
                "error, pre-pull the images on a machine with registry "
                "access (docker pull pgvector/pgvector:pg16, "
                "qdrant/qdrant, chromadb/chroma, neo4j:5) or configure "
                "a Docker mirror, then rerun this script.",
                file=sys.stderr,
            )
            return int(error.returncode or 1)
        print("storage containers starting; waiting for ports")
        failed = [
            name
            for name, port in PORTS.items()
            if not _wait_port(port, args.wait)
        ]
        if failed:
            print(f"port wait failed for: {', '.join(failed)}", file=sys.stderr)
            _compose("logs", "--tail", "40")
            return 1

    _set_env()
    from scripts.storage_real_smoke import main as smoke_main

    return smoke_main()


if __name__ == "__main__":
    raise SystemExit(main())
