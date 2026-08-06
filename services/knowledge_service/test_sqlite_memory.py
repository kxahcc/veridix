from __future__ import annotations

from services.knowledge_service.models import FactRecord
from services.knowledge_service.sqlite_memory import (
    ProjectMemoryStore,
    SqliteProjectMemory,
)


def _seed(memory: SqliteProjectMemory) -> None:
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


def test_sqlite_memory_persists_projection_across_reopen(tmp_path) -> None:
    db = tmp_path / "memory.db"
    memory = SqliteProjectMemory(db, "project_1")
    _seed(memory)
    memory.append(
        FactRecord(
            fact_id="f4",
            subject="compose-dvwa-1:80",
            predicate="finding",
            value="Exposure",
            observed_at="2026-08-01T02:00:00Z",
            metadata={
                "severity": "medium",
                "source": "nuclei",
                "parser_version": "1",
            },
        )
    )
    memory.close()

    reopened = SqliteProjectMemory(db, "project_1")
    statuses = {
        view.fact.fact_id: view.status
        for view in reopened.projection()
    }
    snapshot = reopened.snapshot()
    metadata_fact = next(
        view.fact
        for view in reopened.projection()
        if view.fact.fact_id == "f4"
    )

    assert statuses["f1"] == "conflict"
    assert statuses["f2"] == "conflict"
    assert statuses["f3"] == "stale"
    assert metadata_fact.metadata["source"] == "nuclei"
    assert metadata_fact.metadata["severity"] == "medium"
    assert snapshot.total_facts == 4
    assert snapshot.conflict == 2
    assert snapshot.stale == 1
    assert reopened.facts_for("/admin")[0].status == "conflict"


def test_sqlite_memory_retract_fix_and_clear(tmp_path) -> None:
    memory = SqliteProjectMemory(tmp_path / "memory.db", "project_1")
    _seed(memory)

    memory.fix("/admin", "accepts_role", "owner", reason="verified")
    admin_views = memory.view(subject="/admin")
    assert [view.fact.value for view in admin_views] == ["owner"]
    assert admin_views[0].status == "active"

    memory.forget("f3")
    assert all(
        view.fact.fact_id != "f3"
        for view in memory.view()
    )

    cleared = memory.clear()
    assert cleared == 1
    assert memory.view() == ()


def test_project_memory_store_isolates_projects(tmp_path) -> None:
    store = ProjectMemoryStore(tmp_path / "memory.db")
    first = store.get("project_1")
    second = store.get("project_2")
    _seed(first)
    second.record("/other", "reachable", "true")

    assert len(first.projection()) == 3
    assert len(second.projection()) == 1


def test_record_ids_are_globally_unique_across_projects(tmp_path) -> None:
    store = ProjectMemoryStore(tmp_path / "memory.db")
    first = store.get("project_1")
    second = store.get("project_2")

    fact_a, inserted_a = first.record("/a", "reachable", "true")
    fact_b, inserted_b = second.record("/b", "reachable", "true")

    assert inserted_a is True
    assert inserted_b is True
    assert fact_a.fact_id != fact_b.fact_id


def test_record_deduplicates_identical_active_fact(tmp_path) -> None:
    memory = SqliteProjectMemory(tmp_path / "memory.db", "project_1")

    first, inserted_first = memory.record(
        "/admin",
        "accepts_role",
        "owner",
    )
    second, inserted_second = memory.record(
        "/admin",
        "accepts_role",
        "owner",
    )

    assert inserted_first is True
    assert inserted_second is False
    assert first.fact_id == second.fact_id
    assert len(memory.projection()) == 1


def test_mark_stale_marks_fact_stale_in_projection(tmp_path) -> None:
    memory = SqliteProjectMemory(tmp_path / "memory.db", "project_1")
    fact, _ = memory.record("/admin", "accepts_role", "owner")

    memory.mark_stale(fact.fact_id, reason="replay_mismatch")

    views = memory.view(subject="/admin")
    assert len(views) == 1
    assert views[0].status == "stale"


def test_project_summaries_persist_across_reopen(tmp_path) -> None:
    db = tmp_path / "memory.db"
    first = SqliteProjectMemory(db, "project_1")
    first.append_summary(
        "run abc: tools=nikto; findings=Exposure",
        source_ref="run_abc",
    )
    first.close()

    reopened = SqliteProjectMemory(db, "project_1")
    summaries = reopened.summaries()

    assert len(summaries) == 1
    assert summaries[0]["source_ref"] == "run_abc"
    assert "nikto" in summaries[0]["summary"]
