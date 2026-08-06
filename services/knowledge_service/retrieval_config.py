from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from .embedding_adapters import create_embedding, create_rerank
from .graph_backends import create_knowledge_graph
from .models import KnowledgeChunk
from .vector_backends import create_vector_store


DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"


def resolve_retrieval_config(
    config: dict[str, Any] | None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Normalize a retrieval settings payload into one canonical shape.

    Both the web form shape (embedding/vector_store/graph/rerank objects)
    and the legacy flat worker shape (endpoint/model/graph_backend) are
    accepted. Server profile fills missing defaults from environment
    variables so a clean install gets pgvector + Neo4j + Ollama-style
    embeddings without manual JSON editing.
    """
    env = env if env is not None else os.environ
    source = dict(config or {})
    embedding = _normalize_embedding(source, env)
    vector = _normalize_vector(source, env)
    graph = _normalize_graph(source, env)
    rerank = _normalize_rerank(source, env)
    return {
        "embedding": embedding,
        "vector_store": vector,
        "graph": graph,
        "rerank": rerank,
        "level": str(source.get("level") or "hybrid"),
        "fusion": str(
            source.get("fusion")
            or env.get("VERIDIX_RETRIEVAL_FUSION")
            or "rrf"
        ),
        "deadline_seconds": str(
            source.get("deadline_seconds")
            or env.get("VERIDIX_RETRIEVAL_DEADLINE")
            or "8"
        ),
        "min_vector_score": str(
            source.get("min_vector_score")
            or env.get("VERIDIX_RETRIEVAL_MIN_VECTOR_SCORE")
            or "0"
        ),
    }


def build_retrieval_components(
    config: dict[str, Any],
    *,
    runtime_dir: str | Path,
) -> dict[str, Any]:
    """Instantiate live adapters for every enabled retrieval backend."""
    embedding = create_embedding(
        config.get("embedding") or {},
        runtime_dir=runtime_dir,
    )
    try:
        rerank = create_rerank(
            config.get("rerank") or {},
            runtime_dir=runtime_dir,
        )
    except ImportError:
        rerank = None
    vector_store = create_vector_store(
        config.get("vector_store") or {},
        runtime_dir=runtime_dir,
    )
    graph_store = create_knowledge_graph(
        config.get("graph") or {},
        runtime_dir=runtime_dir,
    )
    return {
        "embedding": embedding,
        "rerank": rerank,
        "vector_store": vector_store,
        "graph_store": graph_store,
    }


def probe_retrieval_config(
    config: dict[str, Any],
    *,
    runtime_dir: str | Path,
) -> dict[str, Any]:
    """Run a real round-trip against each configured retrieval backend."""
    config = resolve_retrieval_config(config)
    components = build_retrieval_components(config, runtime_dir=runtime_dir)
    result: dict[str, Any] = {}
    result["embedding"] = _probe_embedding(components["embedding"])
    result["rerank"] = _probe_rerank(components["rerank"])
    dimension = _embedding_dimension(components["embedding"])
    result["vector_store"] = _probe_vector(
        components["vector_store"],
        dimension=dimension,
    )
    result["graph"] = _probe_graph(components["graph_store"])
    return result


def _normalize_embedding(
    source: dict[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    nested = source.get("embedding") or {}
    if not isinstance(nested, dict):
        nested = {}
    backend = nested.get("backend") or source.get("embedding_backend")
    if backend in ("openai",):
        backend = "openai_compatible"
    endpoint = nested.get("endpoint") or source.get("endpoint")
    model = nested.get("model") or source.get("model")
    api_key_ref = nested.get("api_key_ref") or source.get("api_key_ref")
    timeout_seconds = (
        nested.get("timeout_seconds")
        or source.get("embedding_timeout_seconds")
        or env.get("VERIDIX_EMBEDDING_TIMEOUT")
    )
    keep_alive = (
        nested.get("keep_alive")
        or env.get("VERIDIX_EMBEDDING_KEEP_ALIVE")
        or ""
    )
    if backend == "ollama":
        endpoint = endpoint or env.get("VERIDIX_EMBEDDING_ENDPOINT") or (
            "http://127.0.0.1:11434/v1"
        )
        model = model or env.get("VERIDIX_EMBEDDING_MODEL")
    elif not endpoint and env.get("VERIDIX_EMBEDDING_ENDPOINT"):
        endpoint = env["VERIDIX_EMBEDDING_ENDPOINT"]
        model = model or env.get("VERIDIX_EMBEDDING_MODEL")
    if backend in ("", None):
        backend = "openai_compatible" if endpoint and model else "none"
    return {
        "backend": str(backend or "none"),
        "endpoint": str(endpoint or ""),
        "model": str(model or ""),
        "api_key_ref": str(api_key_ref or ""),
        "timeout_seconds": str(timeout_seconds or ""),
        "keep_alive": str(keep_alive or ""),
    }


def _normalize_vector(
    source: dict[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    nested = source.get("vector_store") or {}
    if not isinstance(nested, dict):
        nested = {}
    backend = nested.get("type") or source.get("vector_backend")
    if not backend:
        backend = env.get("VERIDIX_VECTOR_BACKEND")
    if not backend:
        if _is_server(env) or env.get("VERIDIX_PGVECTOR_URL"):
            backend = "pgvector"
        elif env.get("VERIDIX_QDRANT_URL"):
            backend = "qdrant"
        elif env.get("VERIDIX_CHROMA_URL"):
            backend = "chroma"
        else:
            backend = "sqlite"
    backend = str(backend)
    vector: dict[str, Any] = {"type": backend}
    if backend == "pgvector":
        database_url = (
            nested.get("database_url")
            or source.get("pgvector_url")
            or env.get("VERIDIX_PGVECTOR_URL")
        )
        vector["database_url"] = str(database_url or "")
    elif backend == "qdrant":
        vector["url"] = str(
            nested.get("url")
            or env.get("VERIDIX_QDRANT_URL")
            or "http://127.0.0.1:6333"
        )
        vector["collection"] = str(
            nested.get("collection") or "veridix_chunks"
        )
        if nested.get("api_key"):
            vector["api_key"] = str(nested["api_key"])
    elif backend == "chroma":
        vector["url"] = str(
            nested.get("url")
            or env.get("VERIDIX_CHROMA_URL")
            or "http://127.0.0.1:8001"
        )
        vector["collection"] = str(
            nested.get("collection") or "veridix_chunks"
        )
    elif backend != "sqlite":
        raise ValueError(f"unsupported vector_store type: {backend}")
    return vector


def _normalize_graph(
    source: dict[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    nested = source.get("graph") or {}
    if not isinstance(nested, dict):
        nested = {}
    legacy = source.get("graph_backend") or {}
    if isinstance(legacy, dict):
        backend = nested.get("backend") or legacy.get("type")
        nested = {**legacy, **nested}
    else:
        backend = nested.get("backend") or legacy
    if not backend:
        backend = (
            "neo4j"
            if _is_server(env) or env.get("VERIDIX_NEO4J_URI")
            else "sqlite"
        )
    backend = str(backend)
    graph: dict[str, Any] = {"backend": backend}
    if backend == "neo4j":
        graph["uri"] = str(
            nested.get("uri")
            or env.get("VERIDIX_NEO4J_URI")
            or "bolt://127.0.0.1:7687"
        )
        graph["user"] = str(
            nested.get("user") or env.get("VERIDIX_NEO4J_USER") or "neo4j"
        )
        graph["password"] = str(
            nested.get("password") or env.get("VERIDIX_NEO4J_PASSWORD") or ""
        )
    elif backend != "sqlite":
        raise ValueError(f"unsupported graph backend: {backend}")
    return graph


def _normalize_rerank(
    source: dict[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    nested = source.get("rerank") or {}
    if not isinstance(nested, dict):
        nested = {}
    enabled = (
        bool(nested.get("enabled"))
        or bool(source.get("rerank_enabled"))
        or str(env.get("VERIDIX_RERANK_ENABLED") or "").lower()
        in ("1", "true", "yes")
    )
    backend = nested.get("backend") or env.get("VERIDIX_RERANK_BACKEND")
    model = (
        nested.get("model")
        or source.get("rerank_model")
        or env.get("VERIDIX_RERANK_MODEL")
        or DEFAULT_RERANK_MODEL
    )
    endpoint = (
        nested.get("endpoint")
        or source.get("rerank_endpoint")
        or env.get("VERIDIX_RERANK_ENDPOINT")
    )
    api_key_ref = (
        nested.get("api_key_ref")
        or source.get("rerank_api_key_ref")
        or env.get("VERIDIX_RERANK_API_KEY_REF")
    )
    timeout_seconds = (
        nested.get("timeout_seconds")
        or source.get("rerank_timeout_seconds")
        or env.get("VERIDIX_RERANK_TIMEOUT")
    )
    if enabled and not backend:
        backend = "openai_compatible" if endpoint else "fastembed"
    if backend == "openai_compatible" and not endpoint:
        enabled = False
    return {
        "enabled": enabled,
        "backend": str(backend or "fastembed"),
        "endpoint": str(endpoint or ""),
        "model": str(model),
        "api_key_ref": str(api_key_ref or ""),
        "timeout_seconds": str(timeout_seconds or ""),
    }


def _is_server(env: Mapping[str, str]) -> bool:
    return str(env.get("VERIDIX_STORAGE_PROFILE") or "desktop") == "server"


def _probe_embedding(embedding) -> dict[str, Any]:
    if embedding is None:
        return {"status": "not_configured", "detail": "未配置嵌入模型"}
    try:
        vector = embedding.embed_query("veridix security probe")
        return {
            "status": "ok",
            "detail": f"维度 {len(vector)}",
        }
    except Exception as error:
        return {
            "status": "failed",
            "detail": f"{type(error).__name__}: {error}",
        }


def _probe_rerank(rerank) -> dict[str, Any]:
    if rerank is None:
        return {"status": "not_configured", "detail": "未启用重排"}
    try:
        chunks = [
            KnowledgeChunk(
                chunk_id="probe-a",
                source_ref="probe",
                content="SQL injection prevention checklist",
            ),
            KnowledgeChunk(
                chunk_id="probe-b",
                source_ref="probe",
                content="cooking recipe for dinner",
            ),
        ]
        ranked = rerank.rerank("SQL injection", chunks)
        return {
            "status": "ok",
            "detail": f"样本 {len(ranked)} 条",
        }
    except Exception as error:
        return {
            "status": "failed",
            "detail": f"{type(error).__name__}: {error}",
        }


def _embedding_dimension(embedding) -> int | None:
    if embedding is None:
        return None
    try:
        return len(embedding.embed_query("veridix security probe"))
    except Exception:
        return None


def _probe_vector(vector_store, dimension: int | None = None) -> dict[str, Any]:
    if vector_store is None:
        return {"status": "not_configured", "detail": "未配置向量库"}
    probe_id = "__veridix_probe__"
    vector = (
        [0.01] * dimension
        if dimension is not None and dimension > 0
        else [0.1, 0.2, 0.3]
    )
    try:
        vector_store.upsert(probe_id, vector, "probe")
        hits = vector_store.search(vector, limit=1)
        vector_store.delete(probe_id)
        return {
            "status": "ok",
            "detail": f"写入/检索/删除成功（命中 {len(hits)}）",
        }
    except Exception as error:
        return {
            "status": "failed",
            "detail": f"{type(error).__name__}: {error}",
        }


def _probe_graph(graph_store) -> dict[str, Any]:
    if graph_store is None:
        return {"status": "not_configured", "detail": "未配置知识图谱"}
    try:
        nodes = graph_store.nodes_for_terms(("veridix", "security"))
        return {
            "status": "ok",
            "detail": f"连通，查询命中 {len(nodes)} 节点",
        }
    except Exception as error:
        return {
            "status": "failed",
            "detail": f"{type(error).__name__}: {error}",
        }
