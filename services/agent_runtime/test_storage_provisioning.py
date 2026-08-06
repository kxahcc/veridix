from __future__ import annotations

from services.agent_runtime import storage_provisioning


def test_desktop_profile_keeps_config(monkeypatch) -> None:
    monkeypatch.setenv("VERIDIX_STORAGE_PROFILE", "desktop")

    config = storage_provisioning.ensure_storage_config(
        {"level": "hybrid"},
        runtime_dir="runtime",
    )

    assert config["vector_store"]["type"] == "sqlite"
    assert config["graph"]["backend"] == "sqlite"
    assert config["embedding"]["backend"] == "none"


def test_server_profile_with_autoprovision_off_fills_defaults(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VERIDIX_STORAGE_PROFILE", "server")
    monkeypatch.setenv("VERIDIX_STORAGE_AUTOPROVISION", "0")
    monkeypatch.setenv("VERIDIX_PGVECTOR_URL", "postgresql://u:p@db/v")
    monkeypatch.setenv("VERIDIX_NEO4J_URI", "bolt://127.0.0.1:7687")

    config = storage_provisioning.ensure_storage_config(
        {},
        runtime_dir="runtime",
    )

    assert config["vector_store"]["type"] == "pgvector"
    assert config["graph"]["backend"] == "neo4j"


def test_server_profile_skips_provisioning_without_docker(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VERIDIX_STORAGE_PROFILE", "server")
    monkeypatch.setenv("VERIDIX_STORAGE_AUTOPROVISION", "1")
    monkeypatch.setattr(
        storage_provisioning,
        "_docker_available",
        lambda: False,
    )

    config = storage_provisioning.ensure_storage_config(
        {},
        runtime_dir="runtime",
    )

    assert config["vector_store"]["type"] == "pgvector"
    assert config["graph"]["backend"] == "neo4j"


def test_missing_services_detects_configured_backends(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        storage_provisioning,
        "_tcp_open",
        lambda port: False,
    )

    missing = storage_provisioning._missing_services(
        {
            "vector_store": {"type": "pgvector", "database_url": "x"},
            "graph": {"backend": "neo4j", "uri": "bolt://x"},
        }
    )

    assert missing == ["pgvector", "neo4j"]
