from __future__ import annotations

from services.knowledge_service.memory import ProjectMemory
from services.knowledge_service.models import FactRecord


def _memory() -> ProjectMemory:
    memory = ProjectMemory("project_1")
    memory.append(
        FactRecord(
            fact_id="f1",
            subject="/admin",
            predicate="accepts_role",
            value="user",
            observed_at="2026-08-01T00:00:00Z",
        )
    )
    memory.append(
        FactRecord(
            fact_id="f2",
            subject="/admin",
            predicate="accepts_role",
            value="admin",
            observed_at="2026-08-01T01:00:00Z",
        )
    )
    memory.append(
        FactRecord(
            fact_id="f3",
            subject="/old",
            predicate="reachable",
            value="true",
            observed_at="2026-07-01T00:00:00Z",
            expires_at="2026-07-02T00:00:00Z",
        )
    )
    return memory


def test_memory_view_and_snapshot() -> None:
    memory = _memory()

    statuses = {view.fact.fact_id: view.status for view in memory.view()}
    snapshot = memory.snapshot()

    assert statuses["f1"] == "conflict"
    assert statuses["f2"] == "conflict"
    assert statuses["f3"] == "stale"
    assert snapshot.project_id == "project_1"
    assert snapshot.total_facts == 3
    assert snapshot.conflict == 2
    assert snapshot.stale == 1


def test_memory_fix_resolves_conflict() -> None:
    memory = _memory()

    memory.fix("/admin", "accepts_role", "owner", reason="verified")

    views = memory.view(subject="/admin")
    assert [view.fact.value for view in views] == ["owner"]
    assert views[0].status == "active"
    assert views[0].fact.trust == "human"


def test_memory_forget_and_clear() -> None:
    memory = _memory()

    memory.forget("f3")
    assert all(view.fact.fact_id != "f3" for view in memory.view())

    cleared = memory.clear()
    assert cleared == 2
    assert memory.view() == ()
    snapshot = memory.snapshot()
    assert snapshot.active == 0
    assert snapshot.conflict == 0
    assert snapshot.stale == 0
