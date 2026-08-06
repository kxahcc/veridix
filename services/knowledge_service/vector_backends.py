from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .pgvector_store import PgvectorVectorStore
from .vector_store import SqliteVectorStore, VectorStore


def create_vector_store(
    config: dict[str, Any] | None,
    *,
    runtime_dir: str | Path,
) -> VectorStore | None:
    """Build a vector store from a retrieval config dict.

    Supported backends: sqlite (desktop default), pgvector, qdrant, chroma.
    Weaviate and Milvus are recognized so configuration can declare them,
    but they return a clear unavailable error until adapters land.
    """
    config = config or {}
    backend = str(config.get("type") or "sqlite")
    if backend == "sqlite":
        return SqliteVectorStore(
            str(Path(runtime_dir) / "knowledge-vectors.db")
        )
    if backend == "pgvector":
        database_url = config.get("database_url")
        if not database_url:
            raise ValueError("pgvector vector_store requires database_url")
        return PgvectorVectorStore(database_url=str(database_url))
    if backend == "qdrant":
        return QdrantVectorStore(
            url=str(config["url"]),
            collection=str(config.get("collection") or "veridix_chunks"),
            api_key=config.get("api_key"),
        )
    if backend == "chroma":
        return ChromaVectorStore(
            base_url=str(config["url"]),
            collection=str(config.get("collection") or "veridix_chunks"),
        )
    if backend in ("weaviate", "milvus"):
        raise ValueError(
            f"vector_store type {backend} is declared but its adapter "
            "is not implemented yet; use sqlite, pgvector, qdrant or chroma"
        )
    raise ValueError(f"unsupported vector_store type: {backend}")


class QdrantVectorStore(VectorStore):
    """Qdrant REST adapter with dense + sparse hybrid search."""

    def __init__(
        self,
        *,
        url: str,
        collection: str,
        api_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._collection = collection
        self._headers = (
            {"api-key": api_key} if api_key else {}
        )
        self._timeout = timeout

    def _ensure_collection(self, dimension: int) -> None:
        response = self._request(
            "PUT",
            f"/collections/{self._collection}",
            json={
                "vectors": {
                    "dense": {
                        "size": dimension,
                        "distance": "Cosine",
                    }
                },
                "sparse_vectors": {
                    "sparse": {},
                },
            },
        )
        if response.status_code not in (200, 409):
            response.raise_for_status()

    def upsert(
        self,
        chunk_id: str,
        vector: list[float],
        source_ref: str,
        *,
        sparse: dict | None = None,
        project_id: str = "",
    ) -> None:
        self._ensure_collection(len(vector))
        point_id = _qdrant_id(chunk_id)
        point_vector: dict = {"dense": vector}
        if sparse and sparse.get("indices"):
            point_vector["sparse"] = {
                "indices": sparse["indices"],
                "values": sparse["values"],
            }
        response = self._request(
            "PUT",
            f"/collections/{self._collection}/points?wait=true",
            json={
                "points": [
                    {
                        "id": point_id,
                        "vector": point_vector,
                        "payload": {
                            "source_ref": source_ref,
                            "original_id": chunk_id,
                            "project_id": project_id,
                        },
                    }
                ]
            },
        )
        response.raise_for_status()

    def upsert_batch(self, entries: list[dict]) -> None:
        if not entries:
            return
        dimension = len(entries[0]["vector"])
        self._ensure_collection(dimension)
        points = []
        for entry in entries:
            point_vector: dict = {"dense": entry["vector"]}
            sparse = entry.get("sparse")
            if sparse and sparse.get("indices"):
                point_vector["sparse"] = {
                    "indices": sparse["indices"],
                    "values": sparse["values"],
                }
            points.append(
                {
                    "id": _qdrant_id(entry["chunk_id"]),
                    "vector": point_vector,
                    "payload": {
                        "source_ref": entry["source_ref"],
                        "original_id": entry["chunk_id"],
                        "project_id": entry.get("project_id", ""),
                    },
                }
            )
        response = self._request(
            "PUT",
            f"/collections/{self._collection}/points?wait=true",
            json={"points": points},
        )
        response.raise_for_status()

    def search(
        self,
        query_vector: list[float],
        limit: int = 5,
        *,
        project_id: str | None = None,
    ) -> list[tuple[str, float]]:
        return self._query_payload(
            {
                "query": query_vector,
                "using": "dense",
                "limit": limit,
                "with_payload": True,
                **(self._project_filter(project_id) or {}),
            },
        )

    def search_hybrid(
        self,
        query_vector: list[float],
        *,
        indices: list[int],
        values: list[float],
        limit: int = 5,
        project_id: str | None = None,
    ) -> list[tuple[str, float]]:
        query: dict = {"fusion": "rrf"}
        filter_payload = self._project_filter(project_id)
        return self._query_payload(
            {
                "prefetch": [
                    {
                        "query": query_vector,
                        "using": "dense",
                        "limit": limit,
                    },
                    {
                        "query": {
                            "indices": indices,
                            "values": values,
                        },
                        "using": "sparse",
                        "limit": limit,
                    },
                ],
                "query": query,
                "limit": limit,
                "with_payload": True,
                **filter_payload,
            },
        )

    def _query_payload(
        self,
        payload: dict,
    ) -> list[tuple[str, float]]:
        response = self._request(
            "POST",
            f"/collections/{self._collection}/points/query",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        result = data.get("result")
        if isinstance(result, dict):
            rows = result.get("points", [])
        else:
            rows = result or []
        hits: list[tuple[str, float]] = []
        for item in rows:
            original = (item.get("payload") or {}).get(
                "original_id"
            ) or str(item.get("id"))
            hits.append((str(original), float(item["score"])))
        return hits

    @staticmethod
    def _project_filter(project_id: str | None) -> dict:
        if not project_id:
            return {}
        return {
            "filter": {
                "must": [
                    {
                        "key": "project_id",
                        "match": {"value": project_id},
                    }
                ]
            }
        }

    def delete(self, chunk_id: str) -> None:
        point_id = _qdrant_id(chunk_id)
        response = self._request(
            "POST",
            f"/collections/{self._collection}/points/delete",
            json={"points": [point_id]},
        )
        response.raise_for_status()

    def count(self) -> int:
        response = self._request(
            "GET",
            f"/collections/{self._collection}",
        )
        if response.status_code == 404:
            return 0
        response.raise_for_status()
        return int(response.json().get("result", {}).get("points_count", 0))

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return httpx.request(
                    method,
                    f"{self._url}{path}",
                    headers=self._headers,
                    json=json,
                    timeout=self._timeout,
                    trust_env=False,
                )
            except httpx.TransportError as error:
                last_error = error
                if attempt + 1 < 3:
                    time.sleep(0.5 * (attempt + 1))
        assert last_error is not None
        raise last_error


class ChromaVectorStore(VectorStore):
    """Minimal Chroma HTTP adapter (collection + upsert + query)."""

    def __init__(
        self,
        *,
        base_url: str,
        collection: str,
        timeout: float = 30.0,
    ) -> None:
        self._url = base_url.rstrip("/")
        self._collection = collection
        self._timeout = timeout
        self._collection_id: str | None = None

    def _get_or_create_collection(self) -> str:
        if self._collection_id is not None:
            return self._collection_id
        response = httpx.get(
            _chroma_collections_url(self._url),
            timeout=self._timeout,
            trust_env=False,
        )
        response.raise_for_status()
        for item in response.json():
            if item.get("name") == self._collection:
                self._collection_id = str(item["id"])
                return self._collection_id
        created = httpx.post(
            _chroma_collections_url(self._url),
            json={
                "name": self._collection,
                "configuration": {},
            },
            timeout=self._timeout,
            trust_env=False,
        )
        created.raise_for_status()
        self._collection_id = str(created.json()["id"])
        return self._collection_id

    def upsert(
        self,
        chunk_id: str,
        vector: list[float],
        source_ref: str,
    ) -> None:
        collection_id = self._get_or_create_collection()
        response = httpx.post(
            (
                f"{self._url}/api/v2/tenants/default_tenant/"
                "databases/default_database/collections/"
                f"{collection_id}/add"
            ),
            json={
                "ids": [chunk_id],
                "embeddings": [vector],
                "metadatas": [{"source_ref": source_ref}],
                "documents": [source_ref],
            },
            timeout=self._timeout,
            trust_env=False,
        )
        response.raise_for_status()

    def search(
        self,
        query_vector: list[float],
        limit: int = 5,
    ) -> list[tuple[str, float]]:
        collection_id = self._get_or_create_collection()
        response = httpx.post(
            (
                f"{self._url}/api/v2/tenants/default_tenant/"
                "databases/default_database/collections/"
                f"{collection_id}/query"
            ),
            json={
                "query_embeddings": [query_vector],
                "n_results": limit,
            },
            timeout=self._timeout,
            trust_env=False,
        )
        response.raise_for_status()
        data = response.json()
        ids = (data.get("ids") or [[]])[0]
        distances = (data.get("distances") or [[]])[0]
        return [
            (str(chunk_id), round(1.0 - float(distance), 6))
            for chunk_id, distance in zip(ids, distances)
        ]

    def delete(self, chunk_id: str) -> None:
        collection_id = self._get_or_create_collection()
        response = httpx.post(
            (
                f"{self._url}/api/v2/tenants/default_tenant/"
                "databases/default_database/collections/"
                f"{collection_id}/delete"
            ),
            json={"ids": [chunk_id]},
            timeout=self._timeout,
            trust_env=False,
        )
        response.raise_for_status()

    def count(self) -> int:
        collection_id = self._get_or_create_collection()
        response = httpx.get(
            (
                f"{self._url}/api/v2/tenants/default_tenant/"
                "databases/default_database/collections/"
                f"{collection_id}"
            ),
            timeout=self._timeout,
            trust_env=False,
        )
        response.raise_for_status()
        return int(response.json().get("n_items", 0))


def _qdrant_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"veridix:{chunk_id}"))


def _chroma_collections_url(base_url: str) -> str:
    return (
        f"{base_url}/api/v2/tenants/default_tenant/"
        "databases/default_database/collections"
    )
