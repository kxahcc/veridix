from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from services.knowledge_service.evaluation import EvaluationQuery
from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.models import KnowledgeChunk

from .matrix import MatrixRunner, ModelProfile


DEFAULT_MODELS = ("deepseek-v4-flash", "veridix-lab-flash")


def build_default_store() -> KnowledgeStore:
    store = KnowledgeStore(":memory:")
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c_admin",
            source_ref="docs/admin",
            content="admin panel accepts role user",
            trust="project_trusted",
            subjects=("web",),
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c_auth",
            source_ref="docs/auth",
            content="authentication uses a session cookie",
            trust="project_trusted",
            subjects=("web",),
        )
    )
    store.add_chunk(
        KnowledgeChunk(
            chunk_id="c_health",
            source_ref="docs/health",
            content="health endpoint returns 200",
            trust="project_trusted",
            subjects=("web",),
        )
    )
    return store


def default_queries() -> list[EvaluationQuery]:
    return [
        EvaluationQuery("admin panel role", ("c_admin",)),
        EvaluationQuery("session cookie authentication", ("c_auth",)),
        EvaluationQuery("health endpoint returns", ("c_health",)),
    ]


def run_rag_matrix(
    models: list[str],
    *,
    out_path: str | None = None,
) -> dict:
    store = build_default_store()
    report = MatrixRunner(
        store,
        queries=default_queries(),
    ).run(
        [ModelProfile(name=name, provider="cli", backend="offline") for name in models],
        target_ref="https://lab.example.test",
        node_type="web_discovery",
    )
    payload = {
        "target_ref": "https://lab.example.test",
        "node_type": "web_discovery",
        "generated_at": report.generated_at,
        "models": models,
        "rows": [asdict(row) for row in report.rows],
    }
    if out_path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="run the offline RAG matrix")
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="comma-separated model names",
    )
    parser.add_argument("--out", default=None, help="optional JSON report path")
    args = parser.parse_args()

    models = [name.strip() for name in args.models.split(",") if name.strip()]
    payload = run_rag_matrix(models, out_path=args.out)
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
