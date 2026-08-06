from __future__ import annotations

import sqlite3
import threading

import pytest

from services.control_plane.app.contracts import AgentEvent, CommandEnvelope
from services.control_plane.app.event_store import CommandStore, EventStore
from services.control_plane.app.projection import project_run
from services.control_plane.app.migrations import SchemaMigrator


def test_append_replay_and_projection() -> None:
    store = EventStore()
    events = [
        AgentEvent(
            event_id="evt_1",
            event_type="run.started",
            stream_id="run_1",
            run_id="run_1",
            actor="control",
        ),
        AgentEvent(
            event_id="evt_2",
            event_type="observation.ingested",
            stream_id="run_1",
            run_id="run_1",
            actor="agent-worker",
            payload={"tool": "shell.probe", "stdout": "ok"},
        ),
        AgentEvent(
            event_id="evt_3",
            event_type="run.succeeded",
            stream_id="run_1",
            run_id="run_1",
            actor="agent-worker",
            payload={"stop_reason": "model.finish"},
        ),
    ]

    sequences = [store.append(event).sequence for event in events]
    replayed = store.replay("run_1")
    projection = project_run(replayed)

    assert sequences == [1, 2, 3]
    assert [event.sequence for event in replayed] == [1, 2, 3]
    assert projection["status"] == "succeeded"
    assert projection["event_count"] == 3
    assert projection["observations"][0]["tool"] == "shell.probe"
    assert projection["stop_reason"] == "model.finish"
    store.close()


def test_duplicate_event_id_is_rejected() -> None:
    store = EventStore()
    event = AgentEvent(
        event_id="evt_dup",
        event_type="run.started",
        stream_id="run_1",
        run_id="run_1",
        actor="control",
    )
    store.append(event)

    with pytest.raises(sqlite3.IntegrityError):
        store.append(event)
    store.close()


def test_cursor_replay_after_sequence() -> None:
    store = EventStore()
    for i in range(1, 4):
        store.append(
            AgentEvent(
                event_id=f"evt_{i}",
                event_type="observation.ingested",
                stream_id="run_1",
                run_id="run_1",
                actor="agent-worker",
                payload={"index": i},
            )
        )

    page = store.replay("run_1", after=1)

    assert [event.sequence for event in page] == [2, 3]
    assert store.latest_sequence("run_1") == 3
    store.close()


def test_restart_rebuilds_same_state(tmp_path) -> None:
    db_path = tmp_path / "events.sqlite3"
    first = EventStore(db_path)
    first.append(
        AgentEvent(
            event_id="evt_1",
            event_type="run.started",
            stream_id="run_1",
            run_id="run_1",
            actor="control",
        )
    )
    first.append(
        AgentEvent(
            event_id="evt_2",
            event_type="run.succeeded",
            stream_id="run_1",
            run_id="run_1",
            actor="agent-worker",
            payload={"stop_reason": "model.finish"},
        )
    )
    first.close()

    second = EventStore(db_path)
    replayed = second.replay("run_1")
    projection = project_run(replayed)

    assert [event.sequence for event in replayed] == [1, 2]
    assert projection["status"] == "succeeded"
    assert projection["event_count"] == 2
    second.close()


def test_command_idempotency_returns_original() -> None:
    store = CommandStore()
    first = CommandEnvelope(
        command_id="cmd_1",
        command_type="run.start",
        run_id="run_1",
        idempotency_key="run_1:start:1",
    )
    duplicate = CommandEnvelope(
        command_id="cmd_2",
        command_type="run.start",
        run_id="run_1",
        idempotency_key="run_1:start:1",
    )

    record_1, replayed_1 = store.submit(first)
    record_2, replayed_2 = store.submit(duplicate)

    assert replayed_1 is False
    assert replayed_2 is True
    assert record_1.state == "accepted"
    assert record_2.command_id == "cmd_1"

    rejected = store.reject("run_1:start:1", "policy_denied")
    assert rejected.state == "rejected"
    assert rejected.rejected_reason == "policy_denied"
    store.close()


def test_unknown_event_type_is_preserved_and_ignored_by_projection() -> None:
    store = EventStore()
    store.append(
        AgentEvent(
            event_id="evt_unknown",
            event_type="custom.experimental",
            stream_id="run_1",
            run_id="run_1",
            actor="agent-worker",
            payload={"note": "forward compatible"},
        )
    )

    replayed = store.replay("run_1")
    projection = project_run(replayed)

    assert len(replayed) == 1
    assert replayed[0].event_type == "custom.experimental"
    assert projection["status"] == "queued"
    assert projection["event_count"] == 1
    store.close()


def test_concurrent_append_is_safe(tmp_path) -> None:
    db_path = tmp_path / "events.sqlite3"
    store = EventStore(db_path)

    def worker(prefix: str) -> None:
        for index in range(25):
            store.append(
                AgentEvent(
                    event_id=f"{prefix}_{index}",
                    event_type="observation.ingested",
                    stream_id="run_1",
                    run_id="run_1",
                    actor="agent-worker",
                    payload={"index": index},
                )
            )

    threads = [
        threading.Thread(target=worker, args=(f"w{worker_id}",))
        for worker_id in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    events = store.replay("run_1")
    assert len(events) == 50
    assert len({event.sequence for event in events}) == 50
    assert store.latest_sequence("run_1") == 50
    store.close()


def test_schema_migrator_applies_ordered_migrations(tmp_path) -> None:
    db_path = tmp_path / "migrated.sqlite3"
    store = EventStore(db_path)
    store.close()
    migrator = SchemaMigrator(db_path)

    applied = migrator.apply_all()
    assert applied == ["2"]
    assert migrator.current_version() == "2"
    assert migrator.apply_all() == []

    columns = {
        row[1]
        for row in sqlite3.connect(str(db_path)).execute(
            "PRAGMA table_info(events)"
        ).fetchall()
    }
    assert "trace_id" in columns
    migrator.close()
