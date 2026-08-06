from __future__ import annotations

from services.control_plane.app.thread_safe_sqlite import SqliteResult

import hashlib
import json
import re
import sqlite3
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from .knowledge_store import KnowledgeStore
from .loader import load_knowledge_dir
from .models import KnowledgeChunk


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    name: str
    kind: str
    location: str
    license: str
    version: str
    content_hash: str
    status: str
    imported_at: str


@dataclass(frozen=True)
class ImportReport:
    source_id: str
    chunk_count: int
    content_hash: str
    skipped_existing: int
    license: str
    version: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'file',
    location TEXT NOT NULL DEFAULT '',
    license TEXT NOT NULL DEFAULT 'unknown',
    version TEXT NOT NULL DEFAULT '1',
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'imported',
    imported_at TEXT NOT NULL
);
"""


class SourceRegistry:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def _execute(self, sql: str, params=()):
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return SqliteResult(cursor.fetchall(), cursor.rowcount)

    def close(self) -> None:
        self._conn.close()

    def upsert(
        self,
        source_id: str,
        *,
        name: str,
        kind: str,
        location: str,
        license: str,
        version: str,
        content_hash: str,
        status: str = "imported",
    ) -> SourceRecord:
        now = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO knowledge_sources (
                    source_id, name, kind, location, license, version,
                    content_hash, status, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    location = excluded.location,
                    license = excluded.license,
                    version = excluded.version,
                    content_hash = excluded.content_hash,
                    status = excluded.status,
                    imported_at = excluded.imported_at
                """,
                (
                    source_id,
                    name,
                    kind,
                    location,
                    license,
                    version,
                    content_hash,
                    status,
                    now,
                ),
            )
        row = self._execute(
            "SELECT * FROM knowledge_sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return _source_from_row(row)

    def get(self, source_id: str) -> SourceRecord | None:
        row = self._execute(
            "SELECT * FROM knowledge_sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return _source_from_row(row) if row is not None else None

    def list(self) -> list[SourceRecord]:
        rows = self._execute(
            "SELECT * FROM knowledge_sources ORDER BY imported_at DESC"
        ).fetchall()
        return [_source_from_row(row) for row in rows]


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def chunk_id(source_ref: str, heading: str) -> str:
    raw = f"{source_ref}:{heading}".encode("utf-8")
    return f"k_{hashlib.sha256(raw).hexdigest()[:20]}"


def parse_markdown_chunks(
    markdown: str,
    *,
    source_ref: str,
    subjects: tuple[str, ...] = (),
    target_refs: tuple[str, ...] = (),
    license: str = "unknown",
    version: str = "1",
    trust: str = "project_trusted",
) -> list[KnowledgeChunk]:
    frontmatter, body = _split_frontmatter(markdown)
    meta_subjects = tuple(
        str(item)
        for item in _frontmatter_list(frontmatter, "subjects")
    )
    merged_subjects = tuple(
        dict.fromkeys((*meta_subjects, *subjects))
    )
    meta_target_refs = tuple(
        str(item)
        for item in _frontmatter_list(frontmatter, "target_refs")
    )
    merged_target_refs = tuple(
        dict.fromkeys((*meta_target_refs, *target_refs))
    )
    headings = _split_headings(body)
    chunks: list[KnowledgeChunk] = []
    for heading, content in headings:
        text = f"{heading}\n\n{content}".strip()
        chunks.append(
            KnowledgeChunk(
                chunk_id=chunk_id(source_ref, heading),
                source_ref=source_ref,
                content=text,
                trust=trust,
                version=version,
                subjects=merged_subjects,
                target_refs=merged_target_refs,
                graph=_frontmatter_graph(frontmatter),
            )
        )
    if not chunks:
        chunks.append(
            KnowledgeChunk(
                chunk_id=chunk_id(source_ref, "document"),
                source_ref=source_ref,
                content=markdown.strip(),
                trust=trust,
                version=version,
                subjects=merged_subjects,
                target_refs=merged_target_refs,
                graph=_frontmatter_graph(frontmatter),
            )
        )
    return chunks


class ImportPipeline:
    def __init__(
        self,
        store: KnowledgeStore,
        *,
        registry: SourceRegistry | None = None,
        registry_db: str | Path = ":memory:",
        graph_store=None,
    ) -> None:
        self._store = store
        self._registry = registry or SourceRegistry(registry_db)
        self._graph_store = graph_store

    def close(self) -> None:
        self._registry.close()

    def import_markdown(
        self,
        content: str,
        *,
        source_id: str,
        name: str = "",
        location: str = "",
        license: str = "unknown",
        version: str = "1",
        project_id: str = "",
        subjects: tuple[str, ...] = (),
        target_refs: tuple[str, ...] = (),
        trust: str = "project_trusted",
    ) -> ImportReport:
        digest = content_hash(content)
        chunks = parse_markdown_chunks(
            content,
            source_ref=source_id,
            subjects=subjects,
            target_refs=target_refs,
            license=license,
            version=version,
            trust=trust,
        )
        skipped = 0
        existing = {
            chunk.chunk_id for chunk in self._store.list_chunks()
        }
        for chunk in chunks:
            chunk = KnowledgeChunk(
                **{
                    **chunk.__dict__,
                    "project_id": project_id,
                }
            )
            if chunk.chunk_id in existing:
                skipped += 1
                self._store.update_chunk(chunk)
            else:
                self._store.add_chunk(chunk)
            if self._graph_store is not None:
                self._graph_store.register_chunk_graph(chunk)
        self._registry.upsert(
            source_id,
            name=name or source_id,
            kind="markdown",
            location=location,
            license=license,
            version=version,
            content_hash=digest,
        )
        return ImportReport(
            source_id=source_id,
            chunk_count=len(chunks),
            content_hash=digest,
            skipped_existing=skipped,
            license=license,
            version=version,
        )

    def import_file(
        self,
        path: str | Path,
        *,
        source_id: str,
        license: str = "unknown",
        version: str = "1",
        project_id: str = "",
        subjects: tuple[str, ...] = (),
        target_refs: tuple[str, ...] = (),
    ) -> ImportReport:
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        return self.import_markdown(
            text,
            source_id=source_id,
            name=path.stem,
            location=str(path),
            license=license,
            version=version,
            project_id=project_id,
            subjects=subjects,
            target_refs=target_refs,
        )

    def import_document_bytes(
        self,
        content: bytes,
        *,
        filename: str,
        source_id: str,
        license: str = "unknown",
        version: str = "1",
        project_id: str = "",
        subjects: tuple[str, ...] = (),
        target_refs: tuple[str, ...] = (),
    ) -> ImportReport:
        text = extract_document_text(content, filename)
        return self.import_markdown(
            text,
            source_id=source_id,
            name=Path(filename).stem,
            location=filename,
            license=license,
            version=version,
            project_id=project_id,
            subjects=subjects,
            target_refs=target_refs,
        )

    def import_directory(
        self,
        directory: str | Path,
        *,
        source_prefix: str,
        license: str = "unknown",
        version: str = "1",
        project_id: str = "",
        subjects: tuple[str, ...] = (),
        target_refs: tuple[str, ...] = (),
    ) -> list[ImportReport]:
        directory = Path(directory)
        reports: list[ImportReport] = []
        for path in sorted(directory.rglob("*.md")):
            source_id = f"{source_prefix}/{path.relative_to(directory)}"
            reports.append(
                self.import_file(
                    path,
                    source_id=source_id,
                    license=license,
                    version=version,
                    project_id=project_id,
                    subjects=subjects,
                    target_refs=target_refs,
                )
            )
        json_files = sorted(directory.glob("*.json"))
        if json_files:
            load_knowledge_dir(
                self._store,
                directory,
                graph_store=self._graph_store,
            )
            reports.append(
                ImportReport(
                    source_id=f"{source_prefix}/json",
                    chunk_count=len(json_files),
                    content_hash="",
                    skipped_existing=0,
                    license=license,
                    version=version,
                )
            )
        return reports

    def sources(self) -> list[SourceRecord]:
        return self._registry.list()

    def source(self, source_id: str) -> SourceRecord | None:
        return self._registry.get(source_id)


def _source_from_row(row: sqlite3.Row) -> SourceRecord:
    return SourceRecord(
        source_id=row["source_id"],
        name=row["name"],
        kind=row["kind"],
        location=row["location"],
        license=row["license"],
        version=row["version"],
        content_hash=row["content_hash"],
        status=row["status"],
        imported_at=row["imported_at"],
    )


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if match is None:
        return {}, text
    data: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if value.strip():
            data[key.strip()] = value.strip().strip('"\'')
    return data, text[match.end() :]


def _frontmatter_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, "")
    if isinstance(value, list):
        return [str(item) for item in value]
    return [
        item.strip()
        for item in str(value).replace("[", "").replace("]", "").split(",")
        if item.strip()
    ]


def _frontmatter_graph(data: dict[str, Any]) -> dict[str, Any] | None:
    raw = data.get("graph")
    if not raw:
        return None
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return None


def _split_headings(body: str) -> list[tuple[str, str]]:
    lines = body.splitlines()
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_heading:
                sections.append((current_heading, "\n".join(current)))
            current_heading = line[3:].strip()
            current = []
        else:
            current.append(line)
    if current_heading:
        sections.append((current_heading, "\n".join(current)))
    elif body.strip():
        sections.append(("document", body.strip()))
    return sections


def extract_document_text(content: bytes, filename: str) -> str:
    """Extract plain text from md/txt/pdf/docx document bytes."""
    suffix = Path(filename).suffix.lower()
    if suffix in (".md", ".markdown", ".txt"):
        return content.decode("utf-8", errors="replace")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise ValueError(
                "pypdf is required to import PDF files"
            ) from error
        reader = PdfReader(BytesIO(content))
        return "\n\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                xml = archive.read("word/document.xml").decode(
                    "utf-8",
                    errors="replace",
                )
        except KeyError as error:
            raise ValueError("invalid docx file") from error
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        return "\n".join(
            line.strip()
            for line in text.splitlines()
            if line.strip()
        )
    raise ValueError(f"unsupported document type {suffix}")
