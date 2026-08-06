from __future__ import annotations

from pathlib import Path

import pytest

from services.knowledge_service import retrieval_config


def test_rerank_import_error_degrades_to_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def raise_missing_fastembed(*args, **kwargs):
        raise ImportError("fastembed is not installed")

    monkeypatch.setattr(
        retrieval_config,
        "create_rerank",
        raise_missing_fastembed,
    )
    monkeypatch.setattr(
        retrieval_config,
        "create_embedding",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        retrieval_config,
        "create_vector_store",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        retrieval_config,
        "create_knowledge_graph",
        lambda *args, **kwargs: None,
    )

    components = retrieval_config.build_retrieval_components(
        {"rerank": {"enabled": True, "backend": "fastembed", "model": "m"}},
        runtime_dir=tmp_path,
    )
    assert components["rerank"] is None
