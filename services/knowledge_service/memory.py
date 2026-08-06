from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import FactRecord, utc_now


@dataclass(frozen=True)
class FactView:
    fact: FactRecord
    status: str


@dataclass(frozen=True)
class MemorySnapshot:
    project_id: str
    total_facts: int
    active: int
    conflict: int
    stale: int
    taken_at: str


def project_fact_views(
    facts: Iterable[FactRecord],
    *,
    now: str | None = None,
    subject: str | None = None,
) -> tuple[FactView, ...]:
    """Derive active/stale/conflict views from append-only fact records."""
    facts = tuple(facts)
    now = now or max(
        (fact.observed_at for fact in facts),
        default="2999-01-01T00:00:00Z",
    )
    retracted_ids = {
        fact.value
        for fact in facts
        if fact.predicate == "retracts" and fact.status == "retracted"
    }
    records = [
        fact
        for fact in facts
        if fact.status != "retracted"
        and fact.fact_id not in retracted_ids
        and fact.predicate != "stale"
        and (subject is None or fact.subject == subject)
    ]
    stale_ids = {
        fact.value
        for fact in facts
        if fact.predicate == "stale" and fact.status != "retracted"
    }
    groups: dict[tuple[str, str], list[FactRecord]] = {}
    for fact in records:
        groups.setdefault((fact.subject, fact.predicate), []).append(fact)
    views: list[FactView] = []
    for group in groups.values():
        live = [
            fact
            for fact in group
            if fact.expires_at is None or fact.expires_at >= now
        ]
        distinct = {fact.value for fact in live}
        for fact in sorted(group, key=lambda f: f.observed_at, reverse=True):
            if (
                fact.expires_at is not None and fact.expires_at < now
            ) or fact.fact_id in stale_ids:
                status = "stale"
            elif len(distinct) > 1:
                status = "conflict"
            else:
                status = "active"
            views.append(FactView(fact=fact, status=status))
    return tuple(sorted(views, key=lambda view: view.fact.observed_at, reverse=True))


class ProjectMemory:
    """Append-only fact board; projection derives active/stale/conflict views."""

    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._facts: list[FactRecord] = []

    @property
    def project_id(self) -> str:
        return self._project_id

    def append(self, fact: FactRecord) -> FactRecord:
        self._facts.append(fact)
        return fact

    def replay(self) -> tuple[FactRecord, ...]:
        return tuple(self._facts)

    def projection(
        self,
        *,
        now: str | None = None,
        subject: str | None = None,
    ) -> tuple[FactView, ...]:
        return project_fact_views(
            self._facts,
            now=now,
            subject=subject,
        )

    def facts_for(self, subject: str, *, now: str | None = None) -> tuple[FactView, ...]:
        return tuple(
            view
            for view in self.projection(now=now, subject=subject)
            if view.status in ("active", "conflict")
        )

    def retract(self, fact_id: str, *, reason: str = "human_correction") -> FactRecord:
        target = next((fact for fact in self._facts if fact.fact_id == fact_id), None)
        if target is None:
            raise KeyError(fact_id)
        return self.append(
            FactRecord(
                fact_id=self._next_id("fact_retract"),
                subject=target.subject,
                predicate="retracts",
                value=fact_id,
                target=self._project_id,
                source_refs=(reason,),
                confidence=1.0,
                trust="human",
                observed_at=utc_now(),
                status="retracted",
            )
        )

    def view(
        self,
        *,
        now: str | None = None,
        subject: str | None = None,
    ) -> tuple[FactView, ...]:
        return self.projection(now=now, subject=subject)

    def fix(
        self,
        subject: str,
        predicate: str,
        value: str,
        *,
        reason: str = "human_fix",
    ) -> FactRecord:
        for view in self.projection(subject=subject):
            if view.fact.predicate == predicate and view.status in (
                "active",
                "conflict",
            ):
                self.retract(view.fact.fact_id, reason=f"fix:{reason}")
        return self.append(
            FactRecord(
                fact_id=self._next_id("fact_fix"),
                subject=subject,
                predicate=predicate,
                value=value,
                target=self._project_id,
                source_refs=(reason,),
                confidence=1.0,
                trust="human",
                status="active",
            )
        )

    def forget(self, fact_id: str, *, reason: str = "human_forget") -> FactRecord:
        return self.retract(fact_id, reason=reason)

    def mark_stale(
        self,
        fact_id: str,
        *,
        reason: str = "replay_mismatch",
    ) -> FactRecord:
        target = next(
            (fact for fact in self._facts if fact.fact_id == fact_id),
            None,
        )
        if target is None:
            raise KeyError(fact_id)
        return self.append(
            FactRecord(
                fact_id=self._next_id("fact_stale"),
                subject=target.subject,
                predicate="stale",
                value=fact_id,
                target=self._project_id,
                source_refs=(reason,),
                confidence=1.0,
                trust="human",
                observed_at=utc_now(),
            )
        )

    def clear(self, *, reason: str = "memory_cleared") -> int:
        targets = [
            fact
            for fact in self._facts
            if fact.status != "retracted"
            and fact.predicate != "retracts"
            and fact.fact_id not in self._retracted_ids()
        ]
        for fact in targets:
            self.retract(fact.fact_id, reason=reason)
        return len(targets)

    def snapshot(self, *, now: str | None = None) -> MemorySnapshot:
        statuses = {
            view.fact.fact_id: view.status
            for view in self.projection(now=now)
        }
        return MemorySnapshot(
            project_id=self._project_id,
            total_facts=len(self._facts),
            active=sum(status == "active" for status in statuses.values()),
            conflict=sum(status == "conflict" for status in statuses.values()),
            stale=sum(status == "stale" for status in statuses.values()),
            taken_at=utc_now(),
        )

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}_{len(self._facts) + 1}"

    def _retracted_ids(self) -> set[str]:
        return {
            fact.value
            for fact in self._facts
            if fact.predicate == "retracts" and fact.status == "retracted"
        }
