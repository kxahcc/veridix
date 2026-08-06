from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from services.knowledge_service.models import KnowledgeChunk

from .contracts import ExecutionRequest, ExecutionResult


MEMORY_TOOL_REFS = ("memory.recall", "memory.record", "memory.status")
AGENT_WRITABLE_TRUST = ("project_observed", "retrieved_untrusted")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expires_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _fact_payload(view: Any) -> dict[str, Any]:
    fact = view.fact
    return {
        "fact_id": fact.fact_id,
        "subject": fact.subject,
        "predicate": fact.predicate,
        "value": fact.value,
        "target": fact.target,
        "source_refs": list(fact.source_refs),
        "confidence": fact.confidence,
        "trust": fact.trust,
        "observed_at": fact.observed_at,
        "expires_at": fact.expires_at,
        "status": view.status,
        "metadata": dict(fact.metadata or {}),
    }


class MemoryToolRunner:
    """Agent-visible project memory tools.

    The runner only reads the projection and appends new observations. It
    cannot retract, fix, clear or promote trust, so model writes stay in the
    same append-only fact model as every other observation.
    """

    def __init__(
        self,
        *,
        memory_provider: Callable[[], Any],
        embedding_provider: Callable[[], Any] | None = None,
        on_memory_changed: Callable[[], None] | None = None,
    ) -> None:
        self._memory_provider = memory_provider
        self._embedding_provider = embedding_provider
        self._on_memory_changed = on_memory_changed

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        try:
            if request.tool_ref == "memory.recall":
                return self._recall(request)
            if request.tool_ref == "memory.record":
                return self._record(request)
            if request.tool_ref == "memory.status":
                return self._status(request)
        except Exception as error:
            return ExecutionResult(
                action_id=request.action_id,
                status="failed",
                exit_code=1,
                stderr=f"memory tool {request.tool_ref} failed: {error}",
            )
        return ExecutionResult(
            action_id=request.action_id,
            status="denied",
            stderr=f"unknown memory tool {request.tool_ref}",
        )

    def _recall(self, request: ExecutionRequest) -> ExecutionResult:
        memory = self._memory_provider()
        query = str(request.input.get("query") or "").strip()
        subject = str(request.input.get("subject") or "").strip()
        predicate = str(request.input.get("predicate") or "").strip()
        include_stale = bool(request.input.get("include_stale", False))
        try:
            limit = max(1, min(50, int(request.input.get("limit") or 20)))
        except (TypeError, ValueError):
            limit = 20

        views = [
            view
            for view in memory.projection()
            if (not subject or subject in view.fact.subject)
            and (not predicate or predicate == view.fact.predicate)
            and (include_stale or view.status in ("active", "conflict"))
        ]
        if query:
            views = self._rank_views(query, views)
        else:
            views = sorted(
                views,
                key=lambda view: view.fact.observed_at,
                reverse=True,
            )
        views = views[:limit]
        facts = [_fact_payload(view) for view in views]
        stdout = json.dumps(
            {
                "count": len(facts),
                "query": query,
                "subject": subject,
                "predicate": predicate,
                "include_stale": include_stale,
                "facts": facts,
            },
            ensure_ascii=True,
        )
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=stdout,
            observations=(
                {
                    "kind": "memory.recall",
                    "query": query,
                    "subject": subject,
                    "predicate": predicate,
                    "count": len(facts),
                    "facts": facts,
                },
            ),
        )

    def _record(self, request: ExecutionRequest) -> ExecutionResult:
        memory = self._memory_provider()
        subject = str(request.input.get("subject") or "").strip()
        predicate = str(request.input.get("predicate") or "").strip()
        value = str(request.input.get("value") or "").strip()
        if not subject or not predicate or not value:
            return ExecutionResult(
                action_id=request.action_id,
                status="denied",
                stderr="memory.record requires subject, predicate and value",
            )
        if len(value) > 4000:
            return ExecutionResult(
                action_id=request.action_id,
                status="denied",
                stderr="memory.record value exceeds 4000 chars",
            )
        trust = str(request.input.get("trust") or "project_observed")
        if trust not in AGENT_WRITABLE_TRUST:
            return ExecutionResult(
                action_id=request.action_id,
                status="denied",
                stderr=(
                    f"memory.record trust must be one of "
                    f"{', '.join(AGENT_WRITABLE_TRUST)}"
                ),
            )
        try:
            confidence = max(0.0, min(1.0, float(request.input.get("confidence") or 0.7)))
        except (TypeError, ValueError):
            confidence = 0.7
        raw_refs = request.input.get("source_refs") or ()
        if isinstance(raw_refs, str):
            refs = tuple(
                item.strip()
                for item in raw_refs.split(",")
                if item.strip()
            )
        else:
            refs = tuple(str(item) for item in raw_refs)
        source_refs = (*refs, f"run://{request.run_id}")
        expires_at: str | None = None
        if request.input.get("expires_in_seconds") is not None:
            try:
                ttl = int(request.input["expires_in_seconds"])
                if ttl <= 0:
                    raise ValueError("expires_in_seconds must be positive")
                expires_at = _expires_at(ttl)
            except (TypeError, ValueError):
                return ExecutionResult(
                    action_id=request.action_id,
                    status="denied",
                    stderr="memory.record expires_in_seconds must be a positive integer",
                )
        metadata = request.input.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            return ExecutionResult(
                action_id=request.action_id,
                status="denied",
                stderr="memory.record metadata must be an object",
            )

        fact, inserted = memory.record(
            subject,
            predicate,
            value,
            target=str(request.input.get("target") or request.input.get("url") or ""),
            source_refs=source_refs,
            confidence=confidence,
            trust=trust,
            expires_at=expires_at,
            metadata=dict(metadata or {}),
        )
        if inserted and self._on_memory_changed is not None:
            self._on_memory_changed()
        payload = {
            "inserted": inserted,
            "fact_id": fact.fact_id,
            "subject": fact.subject,
            "predicate": fact.predicate,
            "value": fact.value,
            "trust": fact.trust,
            "expires_at": fact.expires_at,
            "source_refs": list(fact.source_refs),
        }
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(payload, ensure_ascii=True),
            observations=(
                {
                    "kind": "memory.recorded",
                    "inserted": inserted,
                    "fact": payload,
                },
            ),
        )

    def _status(self, request: ExecutionRequest) -> ExecutionResult:
        memory = self._memory_provider()
        try:
            summary_limit = max(0, min(20, int(request.input.get("summary_limit") or 5)))
        except (TypeError, ValueError):
            summary_limit = 5
        snapshot = memory.snapshot()
        payload = {
            "snapshot": {
                "project_id": memory.project_id,
                "total_facts": snapshot.total_facts,
                "active": snapshot.active,
                "conflict": snapshot.conflict,
                "stale": snapshot.stale,
            },
            "summaries": list(memory.summaries(limit=summary_limit)),
        }
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(payload, ensure_ascii=True),
            observations=(
                {
                    "kind": "memory.status",
                    "snapshot": payload["snapshot"],
                    "summary_count": len(payload["summaries"]),
                },
            ),
        )

    def _rank_views(self, query: str, views: list[Any]) -> list[Any]:
        chunks = [
            KnowledgeChunk(
                chunk_id=view.fact.fact_id,
                source_ref=(
                    view.fact.source_refs[0]
                    if view.fact.source_refs
                    else ""
                ),
                content=(
                    f"{view.fact.subject} {view.fact.predicate} "
                    f"{view.fact.value}"
                ),
                trust=view.fact.trust,
            )
            for view in views
        ]
        try:
            embedding = (
                self._embedding_provider()
                if self._embedding_provider is not None
                else None
            )
            if embedding is not None:
                ranked = embedding.search(query, chunks)
                score_map = dict(ranked)
                return sorted(
                    views,
                    key=lambda view: score_map.get(view.fact.fact_id, 0.0),
                    reverse=True,
                )
        except Exception:
            pass
        query_tokens = set(query.lower().split())
        scored = []
        for view in views:
            text = (
                f"{view.fact.subject} {view.fact.predicate} "
                f"{view.fact.value}".lower()
            )
            overlap = len(query_tokens & set(text.split()))
            scored.append((view, overlap))
        return [view for view, _score in sorted(scored, key=lambda item: item[1], reverse=True)]
