from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .knowledge_store import KnowledgeStore
from .import_pipeline import ImportPipeline
from .models import KnowledgeChunk


ROOT = Path(
    os.environ.get("VERIDIX_ROOT", Path(__file__).resolve().parents[2])
)


def _store(db: str) -> KnowledgeStore:
    path = Path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    return KnowledgeStore(path)


def add_chunks(
    entries: list[dict],
    *,
    db: str,
    project_id: str = "",
) -> int:
    store = _store(db)
    count = 0
    for entry in entries:
        store.add_chunk(
            KnowledgeChunk(
                chunk_id=str(entry["chunk_id"]),
                source_ref=str(entry["source_ref"]),
                content=str(entry["content"]),
                project_id=str(
                    entry.get("project_id") or project_id
                ),
                trust=str(entry.get("trust", "project_trusted")),
                version=str(entry.get("version", "1")),
                subjects=tuple(entry.get("subjects", ())),
            )
        )
        count += 1
    store.close()
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="manage the local knowledge index"
    )
    parser.add_argument(
        "--db",
        default=str(ROOT / "runtime" / "knowledge.db"),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add")
    add.add_argument("--file", default="")
    add.add_argument("--content", default="")
    add.add_argument("--source-ref", default="cli")
    add.add_argument("--chunk-id", default="")
    add.add_argument("--subjects", nargs="*", default=[])
    add.add_argument("--project", default="")

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--project", default="")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--project", default="")

    import_parser = sub.add_parser("import")
    import_parser.add_argument("--file", required=True)
    import_parser.add_argument("--source-ref", default="")
    import_parser.add_argument("--license", default="unknown")
    import_parser.add_argument("--version", default="1")
    import_parser.add_argument("--project", default="")
    import_parser.add_argument("--subjects", nargs="*", default=[])

    args = parser.parse_args()
    if args.command == "import":
        store = _store(args.db)
        pipeline = ImportPipeline(store, registry_db=args.db)
        report = pipeline.import_file(
            args.file,
            source_id=args.source_ref or Path(args.file).stem,
            license=args.license,
            version=args.version,
            project_id=args.project,
            subjects=tuple(args.subjects),
        )
        pipeline.close()
        store.close()
        print(
            json.dumps(
                {
                    "source_id": report.source_id,
                    "chunk_count": report.chunk_count,
                    "content_hash": report.content_hash,
                    "skipped_existing": report.skipped_existing,
                    "license": report.license,
                    "version": report.version,
                }
            )
        )
        return 0
    if args.command == "add":
        if args.file:
            payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
            entries = payload if isinstance(payload, list) else payload.get("chunks", [])
        else:
            if not args.content:
                raise SystemExit("--content or --file is required")
            entries = [
                {
                    "chunk_id": args.chunk_id
                    or f"cli_{abs(hash(args.content)) % 10**6}",
                    "source_ref": args.source_ref,
                    "content": args.content,
                    "subjects": args.subjects,
                }
            ]
        count = add_chunks(
            entries,
            db=args.db,
            project_id=args.project,
        )
        print(json.dumps({"added": count, "db": str(args.db)}))
        return 0
    store = _store(args.db)
    if args.command == "list":
        chunks = [
            {
                "chunk_id": chunk.chunk_id,
                "source_ref": chunk.source_ref,
                "project_id": chunk.project_id,
                "trust": chunk.trust,
                "subjects": list(chunk.subjects),
            }
            for chunk in store.list_chunks(
                project_id=args.project or None,
            )
        ]
    else:
        chunks, excluded = store.search(
            args.query,
            limit=args.limit,
            project_id=args.project or None,
        )
        chunks = [
            {
                "chunk_id": chunk.chunk_id,
                "source_ref": chunk.source_ref,
                "trust": chunk.trust,
                "content": chunk.content,
            }
            for chunk in chunks
        ]
        chunks.append({"excluded": excluded})
    store.close()
    print(json.dumps(chunks, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
