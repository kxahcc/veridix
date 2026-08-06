from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "storage" / "docker-compose.yml"

STACK_SERVICES = {
    "pgvector": {
        "port": int(os.environ.get("VERIDIX_PGVECTOR_PORT", "5433")),
        "env": "VERIDIX_PGVECTOR_URL",
        "default_url": "postgresql://veridix:veridix@127.0.0.1:5433/veridix",
    },
    "qdrant": {
        "port": 6333,
        "env": "VERIDIX_QDRANT_URL",
        "default_url": "http://127.0.0.1:6333",
    },
    "chroma": {
        "port": 8001,
        "env": "VERIDIX_CHROMA_URL",
        "default_url": "http://127.0.0.1:8001",
    },
    "neo4j": {
        "port": 7687,
        "env": "VERIDIX_NEO4J_URI",
        "default_url": "bolt://127.0.0.1:7687",
    },
}


def ensure_storage_config(
    retrieval_config: dict[str, Any],
    *,
    runtime_dir: str | Path,
) -> dict[str, Any]:
    """Apply storage profile defaults and auto-provision missing backends.

    Server profile defaults to pgvector + Neo4j + OpenAI-compatible
    embedding. When a backend is unreachable and Docker is available, the
    stack is started automatically (mirroring the tool environment UX).
    Set VERIDIX_STORAGE_AUTOPROVISION=0 to disable auto-start.
    """
    from services.knowledge_service.retrieval_config import (
        resolve_retrieval_config,
    )

    config = resolve_retrieval_config(retrieval_config)
    profile = os.environ.get("VERIDIX_STORAGE_PROFILE", "desktop")
    if profile != "server":
        return config
    if os.environ.get("VERIDIX_STORAGE_AUTOPROVISION", "1") == "0":
        return config
    _provision_stack(config)
    return config


def _provision_stack(config: dict[str, Any]) -> None:
    services = _missing_services(config)
    if not services:
        return
    if not _docker_available():
        return
    try:
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE),
                "up",
                "-d",
                *services,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return
    deadline = time.time() + 120
    while time.time() < deadline:
        if not _missing_services(config):
            return
        time.sleep(3)


def _missing_services(config: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    vector = config.get("vector_store") or {}
    graph = config.get("graph") or config.get("graph_backend") or {}
    vector_type = str(vector.get("type") or "")
    if vector_type == "pgvector" and not _tcp_open(
        int(os.environ.get("VERIDIX_PGVECTOR_PORT", "5433"))
    ):
        missing.append("pgvector")
    elif vector_type == "qdrant" and not _http_ok(
        str(vector.get("url") or STACK_SERVICES["qdrant"]["default_url"])
    ):
        missing.append("qdrant")
    elif vector_type == "chroma" and not _http_ok(
        (
            str(vector.get("url") or STACK_SERVICES["chroma"]["default_url"])
            + "/api/v2/version"
        )
    ):
        missing.append("chroma")
    if (graph.get("type") or graph.get("backend")) == "neo4j" and not _tcp_open(
        7687
    ):
        missing.append("neo4j")
    return missing


def _tcp_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def _http_ok(url: str) -> bool:
    try:
        response = httpx.get(url, timeout=2, trust_env=False)
        return response.status_code < 400
    except httpx.HTTPError:
        return False


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
