from __future__ import annotations

from io import BytesIO
import zipfile

from services.knowledge_service.graph_store import KnowledgeGraphStore
from services.knowledge_service.import_pipeline import (
    ImportPipeline,
    content_hash,
    parse_markdown_chunks,
)
from services.knowledge_service.knowledge_store import KnowledgeStore


MARKDOWN = """---
subjects: [web_test, verifier]
graph: {"nodes": [{"id": "imported", "type": "playbook", "label": "Imported"}]}
---
# Imported Playbook

## IDOR Check

Use two auth contexts and swap object IDs.

## SSRF Check

Use a one-time callback token.
"""


def test_parse_markdown_chunks_splits_headings_and_frontmatter() -> None:
    chunks = parse_markdown_chunks(
        MARKDOWN,
        source_ref="manual/imported",
        subjects=("host",),
    )

    assert len(chunks) == 2
    assert "IDOR Check" in chunks[0].content
    assert "SSRF Check" in chunks[1].content
    assert "web_test" in chunks[0].subjects
    assert "host" in chunks[0].subjects
    assert chunks[0].graph is not None
    assert chunks[0].chunk_id != chunks[1].chunk_id


def test_import_pipeline_registers_source_and_imports_chunks() -> None:
    store = KnowledgeStore(":memory:")
    graph = KnowledgeGraphStore(":memory:")
    pipeline = ImportPipeline(
        store,
        registry_db=":memory:",
        graph_store=graph,
    )

    report = pipeline.import_markdown(
        MARKDOWN,
        source_id="manual/imported",
        license="CC-BY-4.0",
        version="1.0.0",
    )

    assert report.chunk_count == 2
    assert report.content_hash == content_hash(MARKDOWN)
    assert report.license == "CC-BY-4.0"
    source = pipeline.source("manual/imported")
    assert source is not None
    assert source.version == "1.0.0"
    assert graph.node_count() >= 1
    assert len(store.list_chunks()) == 2
    pipeline.close()


def test_reimport_updates_existing_chunks() -> None:
    store = KnowledgeStore(":memory:")
    pipeline = ImportPipeline(store, registry_db=":memory:")
    pipeline.import_markdown(MARKDOWN, source_id="manual/imported")
    meta_before = store.list_meta()

    report = pipeline.import_markdown(
        MARKDOWN,
        source_id="manual/imported",
        version="1.1.0",
    )

    assert report.skipped_existing == 2
    assert len(store.list_chunks()) == 2
    meta_after = store.list_meta()
    for chunk_id, meta in meta_after.items():
        assert meta["revision"] > meta_before[chunk_id]["revision"]
    pipeline.close()


def test_import_directory_collects_markdown(tmp_path) -> None:
    (tmp_path / "playbook.md").write_text(MARKDOWN, encoding="utf-8")
    store = KnowledgeStore(":memory:")
    pipeline = ImportPipeline(store, registry_db=":memory:")

    reports = pipeline.import_directory(
        tmp_path,
        source_prefix="team",
    )

    assert len(reports) == 1
    assert reports[0].chunk_count == 2
    assert len(store.list_chunks()) == 2
    pipeline.close()


def test_import_document_bytes_txt_and_docx() -> None:
    store = KnowledgeStore(":memory:")
    pipeline = ImportPipeline(store, registry_db=":memory:")

    txt = pipeline.import_document_bytes(
        b"# Uploaded\n\n## Section\nplain text",
        filename="notes.txt",
        source_id="upload/txt",
        version="1.0",
    )
    assert txt.chunk_count >= 1
    assert "Section" in store.list_chunks()[0].content

    docx_bytes = BytesIO()
    with zipfile.ZipFile(docx_bytes, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document><w:body><w:p><w:r><w:t>Word content</w:t>"
            "</w:r></w:p></w:body></w:document>",
        )
    docx = pipeline.import_document_bytes(
        docx_bytes.getvalue(),
        filename="report.docx",
        source_id="upload/docx",
        version="1.0",
    )
    assert docx.chunk_count >= 1
    assert "Word content" in store.list_chunks()[-1].content
    pipeline.close()
