"""Resolve the Docker network shared by lab targets and the tool container.

The default is the unified ``veridix-system`` bridge created by
``deploy/system/docker-compose.yml``. When Docker is available we also
discover the network of a running ``veridix-tools`` container so stale or
missing explicit configuration does not block real tool execution.
"""

from __future__ import annotations

import os


DEFAULT_TOOL_NETWORK = "veridix-system_veridix-net"


def resolve_tool_network(explicit: str | None = None) -> str:
    requested = (explicit or os.environ.get("VERIDIX_TOOL_NETWORK", "")).strip()
    try:
        import docker
        from docker.errors import NotFound

        client = docker.from_env()
        if requested:
            try:
                client.networks.get(requested)
                return requested
            except NotFound:
                pass
        for container in client.containers.list(all=True):
            name = container.name or ""
            if "veridix-tools" in name:
                networks = list(
                    (
                        (container.attrs or {}).get("NetworkSettings", {})
                        or {}
                    )
                    .get("Networks", {})
                    .keys()
                )
                if networks:
                    return networks[0]
        if requested:
            return requested
    except Exception:
        pass
    return requested or DEFAULT_TOOL_NETWORK
