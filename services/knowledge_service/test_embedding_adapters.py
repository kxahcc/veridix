from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.knowledge_service.embedding_adapters import (
    OpenAIEmbeddingAdapter,
    OpenAIRerankAdapter,
    SentenceTransformerEmbeddingAdapter,
)
from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.models import KnowledgeChunk
from services.knowledge_service.retrieval import RetrievalEngine


class FakeEncoder:
    def encode(self, texts, *, normalize_embeddings=False):
        return [
            [1.0, 0.0, 0.0] if "admin" in text else [0.0, 1.0, 0.0]
            for text in texts
        ]


def test_sentence_transformer_adapter_uses_local_encoder() -> None:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="near",
            source_ref="b",
            content="admin token leak",
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="far",
            source_ref="c",
            content="unrelated content",
        )
    )
    adapter = SentenceTransformerEmbeddingAdapter(
        "fixture-model",
        encoder=FakeEncoder(),
    )

    ranked = adapter.search("admin", store.list_chunks())

    assert ranked[0][0] == "near"
    assert ranked[1][0] == "far"


def test_sentence_transformer_adapter_missing_package_raises() -> None:
    import pytest

    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError):
            SentenceTransformerEmbeddingAdapter("fixture-model")
    else:
        pytest.skip("sentence-transformers installed; real model download skipped")


class EmbeddingHandler(BaseHTTPRequestHandler):
    last_body: dict = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        EmbeddingHandler.last_body = body
        if self.path.endswith("/embeddings"):
            inputs = body.get("input", [])
            data = []
            for index, text in enumerate(inputs):
                vector = [1.0, 0.0] if "admin" in text else [0.0, 1.0]
                data.append({"object": "embedding", "index": index, "embedding": vector})
            self._send(200, {"data": data})
            return
        if self.path.endswith("/rerank"):
            documents = body.get("documents", [])
            results = [
                {"index": index, "score": 1.0 if "admin" in doc else 0.0}
                for index, doc in enumerate(documents)
            ]
            self._send(200, {"results": results})
            return
        self._send(404, {"error": "not_found"})

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


def make_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}", thread


def test_embedding_adapter_reorders_by_cosine() -> None:
    server, endpoint, thread = make_server()
    try:
        adapter = OpenAIEmbeddingAdapter(endpoint=endpoint, model="fixture-embed")
        chunks = [
            KnowledgeChunk(chunk_id="far", source_ref="a", content="other topic"),
            KnowledgeChunk(chunk_id="near", source_ref="b", content="admin token leak"),
        ]

        ranked = adapter.search("admin token", chunks)

        assert ranked[0][0] == "near"
        assert ranked[0][1] > ranked[1][1]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_embedding_adapter_sends_keep_alive() -> None:
    server, endpoint, thread = make_server()
    try:
        adapter = OpenAIEmbeddingAdapter(
            endpoint=endpoint,
            model="fixture-embed",
            keep_alive="5m",
        )

        adapter.embed_query("admin")

        assert EmbeddingHandler.last_body.get("keep_alive") == "5m"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_rerank_adapter_and_engine_chain() -> None:
    server, endpoint, thread = make_server()
    try:
        store = KnowledgeStore(":memory:")
        store.add_chunk(
            KnowledgeChunk(chunk_id="far", source_ref="a", content="other topic")
        )
        store.add_chunk(
            KnowledgeChunk(chunk_id="near", source_ref="b", content="admin token leak")
        )
        engine = RetrievalEngine(
            store,
            embedding=OpenAIEmbeddingAdapter(endpoint=endpoint, model="fixture-embed"),
            rerank=OpenAIRerankAdapter(endpoint=endpoint, model="fixture-rerank"),
        )

        result = engine.retrieve(
            "admin token",
            target_ref="t",
            node_type="web_discovery",
            level="embedding",
        )

        assert result.degraded is False
        assert result.level == "embedding"
        assert result.chunks[0].chunk_id == "near"
    finally:
        server.shutdown()
        thread.join(timeout=2)
