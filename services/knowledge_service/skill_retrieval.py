from __future__ import annotations

import threading
import time
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import KnowledgeChunk, SkillManifest
from .skills import SkillRegistry
from .sparse_encoder import tokenize
from .vector_store import SqliteVectorStore


SKILL_PREFIX = "skill:"


@dataclass(frozen=True)
class SkillCandidate:
    skill: SkillManifest
    score: float
    channels: tuple[str, ...]


@dataclass(frozen=True)
class SkillSearchResult:
    query: str
    included: tuple[SkillCandidate, ...]
    omitted: tuple[dict[str, str], ...]
    channels: tuple[str, ...] = ()
    degraded: tuple[str, ...] = ()
    indexed: int = 0


class SkillRetriever:
    """Hybrid skill selection with BM25 + dense vectors + RRF + rerank.

    Skills are indexed into a small standalone vector cache so the main
    knowledge collection stays clean. Lexical scoring always works; vector
    and rerank channels activate when adapters are configured.
    """

    RRF_K = 60

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        embedding=None,
        rerank=None,
        index_path: str | Path | None = None,
        deadline_seconds: float = 8.0,
        fusion: str = "rrf",
        min_vector_score: float = 0.0,
    ) -> None:
        self._registry = registry
        self._embedding = embedding
        self._rerank = rerank
        self._index_path = (
            Path(index_path)
            if index_path is not None
            else Path("runtime/skill-vectors.db")
        )
        self._deadline_seconds = max(0.5, float(deadline_seconds))
        self._fusion = fusion
        self._min_vector_score = min_vector_score
        self._index_lock = threading.RLock()
        self._indexed_count = 0
        self._ready = False

    def index(self, *, force: bool = False) -> int:
        """Embed all registry skills into the local vector cache."""
        with self._index_lock:
            skills = self._registry.list()
            if not skills:
                return 0
            if self._embedding is None:
                return 0
            store = self._open_store()
            try:
                existing = store.count()
            except Exception:
                existing = 0
            meta = self._load_meta()
            current_digest = self._digest(skills)
            if (
                not force
                and existing == len(skills)
                and meta.get("digest") == current_digest
            ):
                self._indexed_count = len(skills)
                self._ready = True
                return existing
            if existing > 0:
                store.close()
                try:
                    self._index_path.unlink()
                except FileNotFoundError:
                    pass
                store = self._open_store()
            texts = [_skill_text(skill) for skill in skills]
            embed_batch = getattr(self._embedding, "embed_batch", None)
            if embed_batch is not None:
                for start in range(0, len(texts), 8):
                    batch_texts = texts[start : start + 8]
                    vectors = embed_batch(batch_texts)
                    for skill, vector in zip(
                        skills[start : start + 8],
                        vectors,
                    ):
                        store.upsert(
                            f"{SKILL_PREFIX}{skill.name}",
                            list(vector),
                            f"skill:{skill.name}@{skill.version}",
                        )
            else:
                embed_query = getattr(self._embedding, "embed_query", None)
                if embed_query is None:
                    return 0
                for skill, text in zip(skills, texts):
                    store.upsert(
                        f"{SKILL_PREFIX}{skill.name}",
                        list(embed_query(text)),
                        f"skill:{skill.name}@{skill.version}",
                    )
            self._indexed_count = len(skills)
            self._ready = True
            self._save_meta(current_digest)
            return len(skills)

    def search(
        self,
        query: str,
        *,
        node_type: str,
        allowed_tools: tuple[str, ...],
        runner: str,
        limit: int = 6,
        require_trigger: bool = True,
    ) -> SkillSearchResult:
        started = time.time()
        skills = self._registry.list()
        query_tokens = _query_tokens(query)
        degraded: list[str] = []
        channels: list[str] = []

        lexical = _lexical_rank(query_tokens, skills)
        channels.append("bm25")
        scored: dict[str, float] = {
            skill.name: self._rrf_score(index)
            for index, (skill, _score) in enumerate(lexical)
        }

        vector_hits: list[tuple[str, float]] = []
        if (
            self._embedding is not None
            and hasattr(self._embedding, "embed_query")
            and self._ready
        ):
            try:
                store = self._open_store()
                query_vector = self._embedding.embed_query(query)
                vector_hits = [
                    (chunk_id, float(score))
                    for chunk_id, score in store.search(
                        query_vector,
                        limit=max(10, len(skills)),
                    )
                    if chunk_id.startswith(SKILL_PREFIX)
                    and float(score) >= self._min_vector_score
                ]
                if vector_hits:
                    channels.append("vector")
            except Exception:
                degraded.append("skill_vector_unavailable")

        if self._fusion == "weighted":
            for chunk_id, score in vector_hits:
                name = chunk_id[len(SKILL_PREFIX) :]
                scored[name] = scored.get(name, 0.0) + 0.5 * score
        elif self._fusion == "vector_first":
            for chunk_id, score in vector_hits:
                name = chunk_id[len(SKILL_PREFIX) :]
                scored[name] = scored.get(name, 0.0) + score
        else:
            for index, (chunk_id, _score) in enumerate(vector_hits):
                name = chunk_id[len(SKILL_PREFIX) :]
                scored[name] = scored.get(name, 0.0) + self._rrf_score(index)

        ranked_names = [
            name
            for name, _score in sorted(
                scored.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        ]
        included: list[SkillCandidate] = []
        omitted: list[dict[str, str]] = []
        for name in ranked_names:
            skill = self._registry.get(name)
            if skill is None:
                continue
            ok, reason = self._registry.project_for_node(
                skill,
                node_type=node_type,
                allowed_tools=allowed_tools,
                runner=runner,
                require_trigger=require_trigger,
            )
            if not ok:
                omitted.append(
                    {
                        "name": skill.name,
                        "version": skill.version,
                        "reason": reason,
                    }
                )
                continue
            included.append(
                SkillCandidate(
                    skill=skill,
                    score=scored[name],
                    channels=tuple(channels),
                )
            )
            if len(included) >= max(10, limit * 3):
                break

        if included and self._rerank is not None:
            remaining = self._remaining(started)
            if remaining > 0.25:
                try:
                    chunks = [
                        KnowledgeChunk(
                            chunk_id=candidate.skill.name,
                            source_ref=(
                                candidate.skill.package_path
                                or candidate.skill.name
                            ),
                            content=_skill_text(candidate.skill),
                        )
                        for candidate in included
                    ]
                    ranked = self._rerank.rerank(query, chunks[:10])
                    rerank_order = {
                        chunk_id: score for chunk_id, score in ranked
                    }
                    included.sort(
                        key=lambda candidate: rerank_order.get(
                            candidate.skill.name,
                            0.0,
                        ),
                        reverse=True,
                    )
                    channels.append("rerank")
                except Exception:
                    degraded.append("skill_rerank_unavailable")

        final = tuple(
            candidate
            for candidate in included[:limit]
            if candidate.score > 0
        )
        return SkillSearchResult(
            query=query,
            included=final,
            omitted=tuple(omitted),
            channels=tuple(channels),
            degraded=tuple(degraded),
            indexed=self._indexed_count,
        )

    def _rrf_score(self, rank: int) -> float:
        return 1.0 / (self.RRF_K + rank + 1)

    def _remaining(self, started: float) -> float:
        return self._deadline_seconds - (time.time() - started)

    def _open_store(self) -> SqliteVectorStore:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        return SqliteVectorStore(str(self._index_path))

    def _meta_path(self) -> Path:
        return Path(str(self._index_path) + ".meta.json")

    def _load_meta(self) -> dict:
        try:
            payload = json.loads(
                self._meta_path().read_text(encoding="utf-8")
            )
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _save_meta(self, digest: str) -> None:
        model = getattr(self._embedding, "_model", "")
        endpoint = getattr(self._embedding, "_endpoint", "")
        self._meta_path().write_text(
            json.dumps(
                {
                    "digest": digest,
                    "model": str(model),
                    "endpoint": str(endpoint),
                    "count": self._indexed_count,
                },
                indent=2,
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )

    def _digest(self, skills: list[SkillManifest]) -> str:
        digest = hashlib.sha256()
        for skill in skills:
            digest.update(_skill_text(skill).encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()


def _skill_text(skill: SkillManifest) -> str:
    return "\n".join(
        [
            f"name: {skill.name}",
            f"description: {skill.description}",
            f"category: {skill.category}",
            f"trigger: {skill.trigger}",
            f"tags: {', '.join(skill.tags)}",
            f"cwe: {', '.join(skill.cwe_ids)}",
            f"tools: {', '.join(skill.required_tools)}",
            skill.content[:1400],
        ]
    )


def _query_tokens(query: str) -> list[str]:
    return tokenize(query)


def _lexical_rank(
    query_tokens: list[str],
    skills: list[SkillManifest],
) -> list[tuple[SkillManifest, float]]:
    if not query_tokens:
        return [
            (skill, 0.001 + _fallback_boost(skill))
            for skill in skills
        ]
    ranked: list[tuple[SkillManifest, float]] = []
    for skill in skills:
        text_tokens = tokenize(_skill_text(skill))
        counts: dict[str, int] = {}
        for token in text_tokens:
            counts[token] = counts.get(token, 0) + 1
        score = 0.0
        for token in query_tokens:
            tf = counts.get(token, 0)
            if tf:
                score += 1.0 + 0.5 * tf
        if skill.name.replace("-", "").replace(".", "") in query_tokens:
            score += 2.0
        if any(token in skill.trigger.lower() for token in query_tokens):
            score += 1.5
        if score > 0:
            ranked.append((skill, score))
    if not ranked:
        return [
            (skill, 0.001 + _fallback_boost(skill))
            for skill in skills
        ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _fallback_boost(skill: SkillManifest) -> float:
    name = skill.name
    if name.startswith(("veridix-", "web-", "verifier", "host.")):
        return 0.5
    if name.startswith("strix-"):
        return 0.3
    if name.startswith("cyberstrike"):
        return 0.2
    return 0.0
