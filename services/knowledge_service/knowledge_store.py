from __future__ import annotations

from services.control_plane.app.thread_safe_sqlite import SqliteResult

import re
import sqlite3
import threading
from pathlib import Path

from .models import KnowledgeChunk, TRUST_ORDER, utc_now

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    content,
    search_tokens,
    chunk_id UNINDEXED,
    source_ref UNINDEXED,
    trust UNINDEXED,
    subjects UNINDEXED,
    target_refs UNINDEXED,
    version UNINDEXED,
    observed_at UNINDEXED,
    expires_at UNINDEXED
);
CREATE TABLE IF NOT EXISTS knowledge_meta (
    chunk_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_projects (
    chunk_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT ''
);
"""


class KnowledgeStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate_fts_schema()
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def _execute(self, sql: str, params=()):
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return SqliteResult(cursor.fetchall(), cursor.rowcount)

    def _migrate_fts_schema(self) -> None:
        columns = {
            row["name"]
            for row in self._execute(
                "PRAGMA table_info(knowledge_fts)"
            ).fetchall()
        }
        if not columns:
            return
        if "search_tokens" in columns and "target_refs" in columns:
            return
        has_target_refs = "target_refs" in columns
        old_rows = self._execute(
            """
            SELECT chunk_id, source_ref, trust, subjects, version,
                   observed_at, expires_at, content
            FROM knowledge_fts
            """
        ).fetchall()
        self._execute("DROP TABLE knowledge_fts")
        self._conn.executescript(SCHEMA)
        for row in old_rows:
            self.add_chunk(
                KnowledgeChunk(
                    chunk_id=row["chunk_id"],
                    source_ref=row["source_ref"],
                    content=row["content"],
                    trust=row["trust"],
                    version=row["version"],
                    subjects=(
                        tuple(row["subjects"].split(","))
                        if row["subjects"]
                        else ()
                    ),
                    target_refs=(
                        _split_target_refs(row["target_refs"])
                        if has_target_refs
                        else ()
                    ),
                    observed_at=row["observed_at"],
                    expires_at=row["expires_at"] or None,
                )
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def clear(self) -> None:
        with self._lock, self._conn:
            self._execute("DELETE FROM knowledge_fts")

    def list_chunks(
        self,
        *,
        project_id: str | None = None,
    ) -> list[KnowledgeChunk]:
        sql = """
            SELECT k.chunk_id, k.source_ref, k.trust, k.subjects, k.version,
                   k.observed_at, k.expires_at, k.content, k.target_refs,
                   COALESCE(kp.project_id, '') AS project_id
            FROM knowledge_fts k
            LEFT JOIN knowledge_projects kp ON kp.chunk_id = k.chunk_id
        """
        params: list = []
        if project_id is not None:
            sql += " WHERE (kp.project_id = ? OR kp.project_id = '')"
            params.append(project_id)
        sql += " ORDER BY k.chunk_id"
        rows = self._execute(
            sql,
            params,
        ).fetchall()
        return [
            KnowledgeChunk(
                chunk_id=row["chunk_id"],
                source_ref=row["source_ref"],
                content=row["content"],
                project_id=row["project_id"],
                trust=row["trust"],
                version=row["version"],
                subjects=(
                    tuple(row["subjects"].split(","))
                    if row["subjects"]
                    else ()
                ),
                target_refs=_split_target_refs(row["target_refs"]),
                observed_at=row["observed_at"],
                expires_at=row["expires_at"] or None,
            )
            for row in rows
        ]

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        for chunk in self.list_chunks():
            if chunk.chunk_id == chunk_id:
                return chunk
        return None

    def add_chunk(self, chunk: KnowledgeChunk) -> KnowledgeChunk:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO knowledge_fts
                    (chunk_id, source_ref, trust, subjects, target_refs,
                     version, observed_at, expires_at, content, search_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    chunk.source_ref,
                    chunk.trust,
                    ",".join(chunk.subjects),
                    ",".join(chunk.target_refs),
                    chunk.version,
                    chunk.observed_at,
                    chunk.expires_at or "",
                    chunk.content,
                    _search_tokens(chunk.content),
                ),
            )
            self._execute(
                """
                INSERT INTO knowledge_projects (chunk_id, project_id)
                VALUES (?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    project_id = excluded.project_id
                """,
                (chunk.chunk_id, chunk.project_id),
            )
            self._execute(
                """
                INSERT INTO knowledge_meta (chunk_id, revision, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    revision = revision + 1,
                    updated_at = excluded.updated_at
                """,
                (chunk.chunk_id, utc_now()),
            )
        return chunk

    def delete_chunk(self, chunk_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._execute(
                "DELETE FROM knowledge_fts WHERE chunk_id = ?",
                (chunk_id,),
            )
            self._execute(
                "DELETE FROM knowledge_meta WHERE chunk_id = ?",
                (chunk_id,),
            )
            self._execute(
                "DELETE FROM knowledge_projects WHERE chunk_id = ?",
                (chunk_id,),
            )
        return cursor.rowcount > 0

    def update_chunk(self, chunk: KnowledgeChunk) -> bool:
        with self._lock, self._conn:
            cursor = self._execute(
                "DELETE FROM knowledge_fts WHERE chunk_id = ?",
                (chunk.chunk_id,),
            )
            existed = cursor.rowcount > 0
        if existed:
            self.add_chunk(chunk)
        return existed

    def list_meta(self) -> dict[str, dict]:
        rows = self._execute(
            "SELECT chunk_id, revision, updated_at FROM knowledge_meta"
        ).fetchall()
        return {
            row["chunk_id"]: {
                "revision": int(row["revision"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def search(
        self,
        query: str,
        *,
        trust_max: str = "project_trusted",
        subject: str | None = None,
        project_id: str | None = None,
        target_ref: str | None = None,
        observed_since: str | None = None,
        observed_until: str | None = None,
        limit: int = 5,
    ) -> tuple[list[KnowledgeChunk], int]:
        max_rank = TRUST_ORDER.get(trust_max, 3)
        allowed_trusts = [
            trust for trust, rank in TRUST_ORDER.items() if rank <= max_rank
        ]
        sql = """
            SELECT k.chunk_id, k.source_ref, k.trust, k.subjects, k.version,
                   k.observed_at, k.expires_at, k.content, k.target_refs,
                   COALESCE(kp.project_id, '') AS project_id,
                   bm25(knowledge_fts) AS score
            FROM knowledge_fts k
            LEFT JOIN knowledge_projects kp ON kp.chunk_id = k.chunk_id
            WHERE knowledge_fts MATCH ?
        """
        params: list = [_fts_query(query)]
        placeholders = ",".join("?" for _ in allowed_trusts)
        sql += f" AND trust IN ({placeholders})"
        params.extend(allowed_trusts)
        if subject:
            sql += " AND subjects LIKE ?"
            params.append(f"%{subject}%")
        if target_ref:
            sql += (
                " AND (k.target_refs = '' "
                "OR ',' || k.target_refs || ',' LIKE ?)"
            )
            params.append(f"%,{target_ref},%")
        if observed_since:
            sql += " AND k.observed_at >= ?"
            params.append(observed_since)
        if observed_until:
            sql += " AND k.observed_at <= ?"
            params.append(observed_until)
        if project_id is not None:
            sql += " AND (kp.project_id = ? OR kp.project_id = '')"
            params.append(project_id)
        sql += " ORDER BY score"
        try:
            rows = self._execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return [], 0
        excluded = max(0, len(rows) - limit)
        rows = rows[:limit]
        chunks = [
            KnowledgeChunk(
                chunk_id=row["chunk_id"],
                source_ref=row["source_ref"],
                content=row["content"],
                project_id=row["project_id"],
                trust=row["trust"],
                version=row["version"],
                subjects=tuple(row["subjects"].split(",")) if row["subjects"] else (),
                target_refs=_split_target_refs(row["target_refs"]),
                observed_at=row["observed_at"],
                expires_at=row["expires_at"] or None,
            )
            for row in rows
        ]
        return chunks, excluded

    def chunks_by_id(self, chunk_ids: tuple[str, ...]) -> list[KnowledgeChunk]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self._execute(
            f"""
            SELECT k.chunk_id, k.source_ref, k.trust, k.subjects, k.version,
                   k.observed_at, k.expires_at, k.content, k.target_refs,
                   COALESCE(kp.project_id, '') AS project_id
            FROM knowledge_fts k
            LEFT JOIN knowledge_projects kp ON kp.chunk_id = k.chunk_id
            WHERE k.chunk_id IN ({placeholders})
            """,
            chunk_ids,
        ).fetchall()
        return [
            KnowledgeChunk(
                chunk_id=row["chunk_id"],
                source_ref=row["source_ref"],
                content=row["content"],
                project_id=row["project_id"],
                trust=row["trust"],
                version=row["version"],
                subjects=(
                    tuple(row["subjects"].split(","))
                    if row["subjects"]
                    else ()
                ),
                target_refs=_split_target_refs(row["target_refs"]),
                observed_at=row["observed_at"],
                expires_at=row["expires_at"] or None,
            )
            for row in rows
        ]


def _split_target_refs(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(item for item in raw.split(",") if item)


def _fts_query(query: str) -> str:
    """Build an FTS query with ASCII words and CJK bigrams.

    Chinese runs are expanded into overlapping bigrams because the default
    unicode61 tokenizer treats a whole CJK run as one token; the indexed
    ``search_tokens`` column stores the same bigrams so both sides align.
    """
    parts = [f'"{token}"' for token in re.findall(r"[A-Za-z0-9_]+", query)]
    for run in re.findall(r"[\u4e00-\u9fff]+", query):
        parts.extend(f'"{token}"' for token in _cjk_lexical_tokens(run))
    return " OR ".join(parts) or '""'


def _search_tokens(content: str) -> str:
    """Tokenize content for CJK-aware FTS5 matching."""
    tokens = re.findall(r"[A-Za-z0-9_]+", content)
    for run in re.findall(r"[\u4e00-\u9fff]+", content):
        tokens.extend(_cjk_lexical_tokens(run))
    return " ".join(dict.fromkeys(tokens))


_SECURITY_PHRASES = (
    "信息收集",
    "子域名",
    "服务识别",
    "边界突破",
    "目录爆破",
    "漏洞利用",
    "内网横向",
    "凭据验证",
    "权限维持",
    "痕迹清理",
    "报告交付",
    "证据复现",
    "文件包含",
    "目录遍历",
    "口令爆破",
    "账号接管",
    "未授权访问",
    "反序列化",
    "命令注入",
    "文件上传",
    "越权访问",
    "逻辑漏洞",
    "渗透测试",
    "安全测试",
    "攻击面",
)


def _cjk_lexical_tokens(run: str) -> list[str]:
    """Domain-aware CJK tokenization: security phrases plus bigrams."""
    tokens: list[str] = []
    index = 0
    while index < len(run):
        matched: str | None = None
        for phrase in _SECURITY_PHRASES:
            if run.startswith(phrase, index):
                matched = phrase
                break
        if matched is not None:
            tokens.append(matched)
            index += len(matched)
        elif index + 1 < len(run):
            tokens.append(run[index : index + 2])
            index += 1
        else:
            tokens.append(run[index])
            index += 1
    return tokens
