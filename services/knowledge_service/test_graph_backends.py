from __future__ import annotations

import pytest

from services.knowledge_service.graph_backends import (
    create_knowledge_graph,
)


def test_create_knowledge_graph_sqlite_default(tmp_path) -> None:
    graph = create_knowledge_graph(None, runtime_dir=tmp_path)
    assert graph is not None
    graph.close()


def test_create_knowledge_graph_neo4j_requires_driver(tmp_path) -> None:
    try:
        import neo4j  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError):
            create_knowledge_graph(
                {
                    "type": "neo4j",
                    "uri": "bolt://127.0.0.1:7687",
                },
                runtime_dir=tmp_path,
            )
    else:
        pytest.skip("neo4j driver installed; real server validation pending")


def test_create_knowledge_graph_neo4j_requires_uri(tmp_path) -> None:
    with pytest.raises(ValueError):
        create_knowledge_graph(
            {"type": "neo4j"},
            runtime_dir=tmp_path,
        )


def test_create_knowledge_graph_rejects_unknown_backend(tmp_path) -> None:
    with pytest.raises(ValueError):
        create_knowledge_graph(
            {"type": "arangodb"},
            runtime_dir=tmp_path,
        )
