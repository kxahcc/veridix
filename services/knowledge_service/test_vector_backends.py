from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.knowledge_service.vector_backends import (
    ChromaVectorStore,
    QdrantVectorStore,
    create_vector_store,
)


class QdrantHandler(BaseHTTPRequestHandler):
    points: dict[str, dict] = {}
    payloads: dict[str, dict] = {}

    def do_GET(self) -> None:
        if self.path.startswith("/collections/"):
            name = self.path.split("/collections/")[1]
            if name:
                self._send(
                    200,
                    {
                        "result": {
                            "points_count": len(self.points)
                        }
                    },
                )
                return
        self._send(200, {"result": {"points_count": 0}})

    def do_PUT(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.startswith("/collections/"):
            if "points" in self.path:
                for point in body["points"]:
                    self.points[str(point["id"])] = point["vector"]
                    self.payloads[str(point["id"])] = point.get(
                        "payload",
                        {},
                    )
            self._send(200, {"result": True})
            return
        self._send(200, {"result": True})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if "/query" in self.path:
            prefetch = body.get("prefetch")
            limit = int(body.get("limit", 5))
            must = body.get("filter", {}).get("must", [])
            project_filter = None
            for condition in must:
                if condition.get("key") == "project_id":
                    project_filter = condition.get("match", {}).get(
                        "value"
                    )
            points = {
                chunk_id: stored
                for chunk_id, stored in self.points.items()
                if project_filter is None
                or self.payloads.get(chunk_id, {}).get("project_id")
                == project_filter
            }
            if prefetch:
                dense = prefetch[0]["query"]
                sparse = prefetch[1]["query"]
                sparse_indices = set(sparse.get("indices", []))
                scored = sorted(
                    (
                        (
                            chunk_id,
                            _hybrid_score(
                                dense,
                                stored.get("dense", []),
                                sparse_indices,
                                stored.get("sparse") or {},
                            ),
                        )
                        for chunk_id, stored in points.items()
                    ),
                    key=lambda item: item[1],
                    reverse=True,
                )[:limit]
            else:
                query = body["query"]
                scored = sorted(
                    (
                        (
                            chunk_id,
                            _cosine(query, stored.get("dense", [])),
                        )
                        for chunk_id, stored in points.items()
                    ),
                    key=lambda item: item[1],
                    reverse=True,
                )[:limit]
            self._send(
                200,
                {
                    "result": {
                        "points": [
                            {
                                "id": chunk_id,
                                "score": score,
                                "payload": self.payloads.get(chunk_id, {}),
                            }
                            for chunk_id, score in scored
                        ]
                    }
                },
            )
            return
        vector = body["vector"]
        if isinstance(vector, dict):
            dense = vector.get("dense", [])
            sparse = vector.get("sparse") or {}
            sparse_indices = set(sparse.get("indices", []))
            scored = sorted(
                (
                    (
                        chunk_id,
                        _hybrid_score(
                            dense,
                            stored.get("dense", []),
                            sparse_indices,
                            stored.get("sparse") or {},
                        ),
                    )
                    for chunk_id, stored in self.points.items()
                ),
                key=lambda item: item[1],
                reverse=True,
            )[: body.get("limit", 5)]
            self._send(
                200,
                {
                    "result": [
                        {
                            "id": chunk_id,
                            "score": score,
                            "payload": self.payloads.get(chunk_id, {}),
                        }
                        for chunk_id, score in scored
                    ]
                },
            )
            return
        scored = sorted(
            (
                (
                    chunk_id,
                    _cosine(vector, stored),
                )
                for chunk_id, stored in self.points.items()
            ),
            key=lambda item: item[1],
            reverse=True,
        )[: body.get("limit", 5)]
        self._send(
            200,
            {
                "result": [
                    {
                        "id": chunk_id,
                        "score": score,
                        "payload": self.payloads.get(chunk_id, {}),
                    }
                    for chunk_id, score in scored
                ]
            },
        )

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # pragma: no cover
        return


class ChromaHandler(BaseHTTPRequestHandler):
    collections: list[dict] = []
    ids: list[str] = []
    embeddings: list[list[float]] = []

    def do_GET(self) -> None:
        self._send(200, self.collections)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if "/collections" in self.path and "/add" not in self.path and "/query" not in self.path:
            collection = {
                "id": f"col_{len(self.collections)}",
                "name": body["name"],
            }
            self.collections.append(collection)
            self._send(201, collection)
            return
        if "/add" in self.path:
            self.ids.extend(body["ids"])
            self.embeddings.extend(body["embeddings"])
            self._send(200, {"ids": body["ids"]})
            return
        if "/query" in self.path:
            query = body["query_embeddings"][0]
            scored = sorted(
                (
                    (chunk_id, _cosine(query, embedding))
                    for chunk_id, embedding in zip(self.ids, self.embeddings)
                ),
                key=lambda item: item[1],
                reverse=True,
            )[: body.get("n_results", 5)]
            self._send(
                200,
                {
                    "ids": [[chunk_id for chunk_id, _ in scored]],
                    "distances": [
                        [round(1.0 - score, 6) for _, score in scored]
                    ],
                },
            )
            return
        self._send(404, {"error": "not_found"})

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # pragma: no cover
        return


def _server(handler) -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}"


def _hybrid_score(
    query_dense: list[float],
    stored_dense: list[float],
    query_sparse_indices: set[int],
    stored_sparse: dict,
) -> float:
    dense_score = _cosine(query_dense, stored_dense)
    overlap = len(
        query_sparse_indices
        & set(stored_sparse.get("indices", []))
    )
    return dense_score + 0.1 * overlap


def test_qdrant_adapter_upserts_and_searches() -> None:
    url = _server(QdrantHandler)
    store = QdrantVectorStore(url=url, collection="chunks")
    store.upsert("c1", [1.0, 0.0, 0.0], "source/c1")

    hits = store.search([1.0, 0.0, 0.0], limit=1)

    assert hits[0][0] == "c1"
    assert hits[0][1] > 0.9


def test_qdrant_adapter_hybrid_sparse_search() -> None:
    url = _server(QdrantHandler)
    store = QdrantVectorStore(url=url, collection="chunks")
    store.upsert(
        "c1",
        [1.0, 0.0, 0.0],
        "source/c1",
        sparse={"indices": [10, 20], "values": [1.0, 1.5]},
    )
    store.upsert("c2", [0.0, 1.0, 0.0], "source/c2")

    hits = store.search_hybrid(
        [1.0, 0.0, 0.0],
        indices=[10, 30],
        values=[1.0, 1.0],
        limit=2,
    )

    assert hits[0][0] == "c1"


def test_qdrant_adapter_upserts_batch() -> None:
    url = _server(QdrantHandler)
    store = QdrantVectorStore(url=url, collection="chunks")

    store.upsert_batch(
        [
            {
                "chunk_id": "c1",
                "vector": [1.0, 0.0, 0.0],
                "source_ref": "source/c1",
                "sparse": {"indices": [5], "values": [1.0]},
            },
            {
                "chunk_id": "c2",
                "vector": [0.0, 1.0, 0.0],
                "source_ref": "source/c2",
            },
        ]
    )

    hits = store.search([1.0, 0.0, 0.0], limit=1)
    assert hits[0][0] == "c1"
    assert store.count() == 2


def test_qdrant_adapter_filters_by_project() -> None:
    url = _server(QdrantHandler)
    store = QdrantVectorStore(url=url, collection="chunks")
    store.upsert("p1", [1.0, 0.0, 0.0], "s/p1", project_id="proj-a")
    store.upsert("p2", [1.0, 0.0, 0.0], "s/p2", project_id="proj-b")

    hits = store.search([1.0, 0.0, 0.0], limit=5, project_id="proj-a")

    assert [item[0] for item in hits] == ["p1"]


def test_chroma_adapter_upserts_and_queries() -> None:
    url = _server(ChromaHandler)
    store = ChromaVectorStore(base_url=url, collection="chunks")
    store.upsert("c1", [1.0, 0.0, 0.0], "source/c1")

    hits = store.search([1.0, 0.0, 0.0], limit=1)

    assert hits[0][0] == "c1"
    assert hits[0][1] > 0.9


def test_create_vector_store_validation(tmp_path) -> None:
    sqlite = create_vector_store({"type": "sqlite"}, runtime_dir=tmp_path)
    assert sqlite is not None
    with pytest.raises(ValueError):
        create_vector_store(
            {"type": "pgvector"},
            runtime_dir=tmp_path,
        )
    with pytest.raises(ValueError):
        create_vector_store(
            {"type": "milvus"},
            runtime_dir=tmp_path,
        )


def _cosine(first: list[float], second: list[float]) -> float:
    import math

    dot = sum(a * b for a, b in zip(first, second))
    norm = math.sqrt(sum(v * v for v in first)) * math.sqrt(
        sum(v * v for v in second)
    )
    return dot / norm if norm else 0.0
