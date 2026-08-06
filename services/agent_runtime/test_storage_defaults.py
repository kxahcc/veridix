from __future__ import annotations

from services.knowledge_service.retrieval_config import (
    resolve_retrieval_config,
)


def test_desktop_profile_keeps_config_unchanged() -> None:
    config = {
        "level": "hybrid",
        "embedding": {"backend": "none"},
        "vector_store": {"type": "sqlite"},
        "graph": {"backend": "sqlite"},
        "rerank": {"enabled": False},
    }

    resolved = resolve_retrieval_config(config)

    assert resolved["level"] == "hybrid"
    assert resolved["embedding"]["backend"] == "none"
    assert resolved["vector_store"]["type"] == "sqlite"
    assert resolved["graph"]["backend"] == "sqlite"
    assert resolved["rerank"]["enabled"] is False


def test_server_profile_fills_mature_backends(monkeypatch) -> None:
    monkeypatch.setenv("VERIDIX_STORAGE_PROFILE", "server")
    monkeypatch.setenv("VERIDIX_PGVECTOR_URL", "postgresql://u:p@db/v")
    monkeypatch.setenv("VERIDIX_NEO4J_URI", "bolt://127.0.0.1:7687")
    monkeypatch.setenv("VERIDIX_EMBEDDING_ENDPOINT", "http://emb/v1")
    monkeypatch.setenv("VERIDIX_EMBEDDING_MODEL", "text-embed")

    resolved = resolve_retrieval_config({"level": "hybrid"})

    assert resolved["vector_store"] == {
        "type": "pgvector",
        "database_url": "postgresql://u:p@db/v",
    }
    assert resolved["graph"]["backend"] == "neo4j"
    assert resolved["graph"]["uri"] == "bolt://127.0.0.1:7687"
    assert resolved["embedding"]["endpoint"] == "http://emb/v1"
    assert resolved["embedding"]["model"] == "text-embed"
    assert resolved["embedding"]["backend"] == "openai_compatible"


def test_desktop_profile_uses_mature_backends_when_env_is_configured(
    monkeypatch,
) -> None:
    monkeypatch.delenv("VERIDIX_STORAGE_PROFILE", raising=False)
    monkeypatch.setenv("VERIDIX_QDRANT_URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("VERIDIX_VECTOR_BACKEND", "qdrant")
    monkeypatch.setenv("VERIDIX_NEO4J_URI", "bolt://127.0.0.1:7687")
    monkeypatch.setenv("VERIDIX_EMBEDDING_ENDPOINT", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("VERIDIX_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("VERIDIX_RERANK_ENABLED", "1")
    monkeypatch.setenv("VERIDIX_RERANK_ENDPOINT", "http://127.0.0.1:11434/v1")

    resolved = resolve_retrieval_config({"level": "hybrid"})

    assert resolved["vector_store"]["type"] == "qdrant"
    assert resolved["graph"]["backend"] == "neo4j"
    assert resolved["embedding"]["backend"] == "openai_compatible"
    assert resolved["rerank"]["enabled"] is True


def test_server_profile_explicit_config_wins(monkeypatch) -> None:
    monkeypatch.setenv("VERIDIX_STORAGE_PROFILE", "server")
    monkeypatch.setenv("VERIDIX_PGVECTOR_URL", "postgresql://u:p@db/v")

    resolved = resolve_retrieval_config(
        {"vector_store": {"type": "qdrant", "url": "http://qdrant:6333"}}
    )

    assert resolved["vector_store"]["type"] == "qdrant"
