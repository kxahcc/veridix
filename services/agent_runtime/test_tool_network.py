from __future__ import annotations

import builtins

import pytest

from services.agent_runtime.tool_network import (
    DEFAULT_TOOL_NETWORK,
    resolve_tool_network,
)


class _FakeNetwork:
    def __init__(self, exists: bool) -> None:
        self._exists = exists

    def __bool__(self) -> bool:
        return self._exists


class _FakeNetworks:
    def __init__(self, existing: set[str]) -> None:
        self._existing = existing

    def get(self, name: str) -> _FakeNetwork:
        if name not in self._existing:
            from docker.errors import NotFound

            raise NotFound(f"network {name} not found")
        return _FakeNetwork(True)


class _FakeContainer:
    def __init__(self, name: str, networks: list[str]) -> None:
        self.name = name
        self.attrs = {"NetworkSettings": {"Networks": {n: {} for n in networks}}}


class _FakeContainers:
    def __init__(self, containers: list[_FakeContainer]) -> None:
        self._containers = containers

    def list(self, *, all: bool = False) -> list[_FakeContainer]:
        return self._containers


class _FakeDockerClient:
    def __init__(self, *, networks: set[str], containers: list[_FakeContainer]) -> None:
        self.networks = _FakeNetworks(networks)
        self.containers = _FakeContainers(containers)


@pytest.fixture()
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> _FakeDockerClient:
    client = _FakeDockerClient(
        networks={"veridix-system_veridix-net"},
        containers=[
            _FakeContainer(
                "veridix-system-veridix-tools-1",
                ["veridix-system_veridix-net"],
            )
        ],
    )
    import docker

    monkeypatch.setattr(docker, "from_env", lambda: client)
    return client


def test_explicit_network_is_used_when_it_exists(
    fake_docker: _FakeDockerClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VERIDIX_TOOL_NETWORK", raising=False)
    assert (
        resolve_tool_network("veridix-system_veridix-net")
        == "veridix-system_veridix-net"
    )


def test_stale_explicit_network_falls_back_to_tool_container(
    fake_docker: _FakeDockerClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VERIDIX_TOOL_NETWORK", raising=False)
    assert resolve_tool_network("compose_dvwa-net") == "veridix-system_veridix-net"


def test_env_network_wins_when_no_explicit_argument(
    fake_docker: _FakeDockerClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VERIDIX_TOOL_NETWORK", "custom-net")
    fake_docker.networks = _FakeNetworks({"custom-net"})
    assert resolve_tool_network() == "custom-net"


def test_default_is_used_when_docker_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VERIDIX_TOOL_NETWORK", raising=False)

    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "docker" or name.startswith("docker."):
            raise ModuleNotFoundError("docker is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert resolve_tool_network() == DEFAULT_TOOL_NETWORK
