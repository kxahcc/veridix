from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .models import KnowledgeChunk
from .retrieval import EmbeddingAdapter


class OpenAIEmbeddingAdapter(EmbeddingAdapter):
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key_ref: str | None = None,
        timeout: float = 60.0,
        keep_alive: str | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._headers = _auth_headers(api_key_ref)
        self._timeout = timeout
        self._keep_alive = keep_alive

    def warmup(self) -> None:
        try:
            self.embed_query("warmup")
        except Exception:
            pass

    def search(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
    ) -> list[tuple[str, float]]:
        response = _post_with_retry(
            f"{self._endpoint}/embeddings",
            headers=self._headers,
            payload={
                "model": self._model,
                "input": [query, *[chunk.content for chunk in chunks]],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        if len(data) != len(chunks) + 1:
            raise ValueError("embedding response count mismatch")
        query_vector = data[0]["embedding"]
        dimension = len(query_vector)
        scored: list[tuple[str, float]] = []
        for chunk, item in zip(chunks, data[1:]):
            vector = item["embedding"]
            if len(vector) != dimension:
                raise ValueError("embedding dimension mismatch")
            scored.append(
                (
                    chunk.chunk_id,
                    _cosine_similarity(query_vector, vector),
                )
            )
        return sorted(scored, key=lambda item: item[1], reverse=True)

    def embed_query(self, query: str) -> list[float]:
        payload: dict[str, Any] = {
            "model": self._model,
            "input": [query],
        }
        if self._keep_alive:
            payload["keep_alive"] = self._keep_alive
        response = _post_with_retry(
            f"{self._endpoint}/embeddings",
            headers=self._headers,
            payload=payload,
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        if not data:
            raise ValueError("embedding response empty")
        return list(data[0]["embedding"])

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
        }
        if self._keep_alive:
            payload["keep_alive"] = self._keep_alive
        response = _post_with_retry(
            f"{self._endpoint}/embeddings",
            headers=self._headers,
            payload=payload,
            timeout=self._timeout,
        )
        if response.status_code in (400, 413, 422):
            if len(texts) == 1:
                response.raise_for_status()
            middle = len(texts) // 2
            return self.embed_batch(
                texts[:middle]
            ) + self.embed_batch(texts[middle:])
        response.raise_for_status()
        data = response.json().get("data", [])
        by_index = {
            int(item.get("index", index)): list(item["embedding"])
            for index, item in enumerate(data)
        }
        return [by_index[index] for index in range(len(texts))]


class OpenAIRerankAdapter:
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key_ref: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._headers = _auth_headers(api_key_ref)
        self._timeout = timeout

    def rerank(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
    ) -> list[tuple[str, float]]:
        response = _post_with_retry(
            f"{self._endpoint}/rerank",
            headers=self._headers,
            payload={
                "model": self._model,
                "query": query,
                "documents": [chunk.content for chunk in chunks],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        by_index = {int(item["index"]): float(item["score"]) for item in results}
        return sorted(
            (
                (chunk.chunk_id, by_index.get(index, 0.0))
                for index, chunk in enumerate(chunks)
            ),
            key=lambda item: item[1],
            reverse=True,
        )


class SentenceTransformerEmbeddingAdapter(EmbeddingAdapter):
    """Local sentence-transformers embedding adapter.

    Uses the optional ``sentence-transformers`` package. Construction
    raises ImportError when the package (or model) is unavailable so the
    retrieval layer can degrade to lexical retrieval instead of faking
    vector hits.
    """

    def __init__(
        self,
        model_name: str,
        *,
        encoder=None,
        timeout: float = 5.0,
    ) -> None:
        if encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as error:
                raise ImportError(
                    "sentence-transformers is not installed; "
                    "use an OpenAI-compatible embedding endpoint instead"
                ) from error
            encoder = SentenceTransformer(model_name)
        self._encoder = encoder
        self._timeout = timeout

    def search(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
    ) -> list[tuple[str, float]]:
        query_vector = self.embed_query(query)
        vectors = self._encode([chunk.content for chunk in chunks])
        return sorted(
            (
                (
                    chunk.chunk_id,
                    _cosine_similarity(query_vector, vector),
                )
                for chunk, vector in zip(chunks, vectors)
            ),
            key=lambda item: item[1],
            reverse=True,
        )

    def embed_query(self, query: str) -> list[float]:
        return list(self._encode([query])[0])

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        encoded = self._encoder.encode(texts, normalize_embeddings=True)
        return [list(vector) for vector in encoded]


class FastembedRerankAdapter:
    """Local cross-encoder reranker backed by fastembed + ONNX Runtime.

    Keeps the model in a persistent cache directory so a real reranker is
    usable offline after the first download. Hugging Face mirror settings
    (HF_ENDPOINT / HF_HUB_DISABLE_XET) are respected from the environment.
    """

    def __init__(
        self,
        *,
        model: str,
        cache_dir: str | Path | None = None,
    ) -> None:
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError as error:
            raise ImportError(
                "fastembed is not installed; use an OpenAI-compatible "
                "rerank endpoint or install fastembed"
            ) from error
        self._model = model
        self._encoder = TextCrossEncoder(
            model_name=model,
            cache_dir=str(cache_dir) if cache_dir else None,
        )

    def rerank(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
    ) -> list[tuple[str, float]]:
        scores = list(
            self._encoder.rerank(
                query,
                [chunk.content for chunk in chunks],
            )
        )
        return sorted(
            (
                (chunk.chunk_id, float(score))
                for chunk, score in zip(chunks, scores)
            ),
            key=lambda item: item[1],
            reverse=True,
        )


def create_embedding(
    config: dict[str, Any] | None,
    *,
    runtime_dir: str | Path,
) -> EmbeddingAdapter | None:
    """Build an embedding adapter from a canonical retrieval config."""
    config = config or {}
    backend = str(config.get("backend") or "none")
    if backend == "none":
        return None
    model = config.get("model")
    if not model:
        return None
    if backend == "local":
        return SentenceTransformerEmbeddingAdapter(str(model))
    if backend in ("openai_compatible", "openai", "ollama"):
        endpoint = str(config.get("endpoint") or "")
        if not endpoint:
            return None
        return OpenAIEmbeddingAdapter(
            endpoint=endpoint,
            model=str(model),
            api_key_ref=config.get("api_key_ref"),
            timeout=float(config.get("timeout_seconds") or 60.0),
            keep_alive=config.get("keep_alive"),
        )
    raise ValueError(f"unsupported embedding backend: {backend}")


def create_rerank(
    config: dict[str, Any] | None,
    *,
    runtime_dir: str | Path,
) -> FastembedRerankAdapter | OpenAIRerankAdapter | None:
    """Build a rerank adapter from a canonical retrieval config."""
    config = config or {}
    if not config.get("enabled", True):
        return None
    backend = str(config.get("backend") or "fastembed")
    model = config.get("model")
    if not model:
        return None
    if backend == "fastembed":
        cache_dir = config.get("cache_dir") or (
            Path(runtime_dir) / "model-cache"
        )
        return FastembedRerankAdapter(
            model=str(model),
            cache_dir=cache_dir,
        )
    if backend in ("openai_compatible", "openai", "ollama"):
        endpoint = str(config.get("endpoint") or "")
        if not endpoint:
            return None
        return OpenAIRerankAdapter(
            endpoint=endpoint,
            model=str(model),
            api_key_ref=config.get("api_key_ref"),
            timeout=float(config.get("timeout_seconds") or 60.0),
        )
    raise ValueError(f"unsupported rerank backend: {backend}")


def _auth_headers(api_key_ref: str | None) -> dict[str, str]:
    if not api_key_ref:
        return {}
    scheme, _, name = api_key_ref.partition(":")
    if scheme == "env" and name:
        value = os.environ.get(name)
        if value:
            return {"Authorization": f"Bearer {value}"}
    return {}


def _post_with_retry(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    attempts: int = 3,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            trust_env = not (
                url.startswith("http://127.0.0.1")
                or url.startswith("http://localhost")
            )
            response = httpx.post(
                url,
                headers={**headers, "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
                trust_env=trust_env,
            )
        except httpx.TransportError as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(0.3 * (attempt + 1))
                continue
            raise
        if response.status_code in (429,) or response.status_code >= 500:
            last_error = httpx.HTTPStatusError(
                f"HTTP {response.status_code}",
                request=response.request,
                response=response,
            )
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
                continue
        return response
    raise last_error  # type: ignore[misc]


def _cosine_similarity(first: list[float], second: list[float]) -> float:
    dot = sum(a * b for a, b in zip(first, second))
    first_norm = math.sqrt(sum(a * a for a in first))
    second_norm = math.sqrt(sum(b * b for b in second))
    if first_norm == 0 or second_norm == 0:
        return 0.0
    return dot / (first_norm * second_norm)
