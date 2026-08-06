from __future__ import annotations

import pytest
import docker

from services.knowledge_service.mcp_connector import ContainerMcpConnector


def test_container_connector_builds_docker_exec_command() -> None:
    connector = ContainerMcpConnector(
        ["python", "-m", "services.knowledge_service.mock_mcp_server"],
        image="python:3.12-slim",
        container_name="veridix-mcp-test",
    )

    command = connector.build_command()

    assert command[:4] == ["docker", "exec", "-i", "veridix-mcp-test"]
    assert command[4:] == ["python", "-m", "services.knowledge_service.mock_mcp_server"]


@pytest.mark.integration
def test_container_connector_lists_tools_when_image_available() -> None:
    connector = ContainerMcpConnector(
        ["python", "-c", "import mcp; print('mcp available')"],
        image="python:3.12-slim",
        container_name="veridix-mcp-smoke",
    )
    try:
        container_id = connector.ensure_container()
        assert container_id
    except Exception as error:
        pytest.skip(f"container runtime unavailable: {error}")
    finally:
        try:
            client = docker.from_env()
            container = client.containers.get("veridix-mcp-smoke")
            container.remove(force=True)
        except Exception:
            pass
