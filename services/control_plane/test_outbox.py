from __future__ import annotations

from services.control_plane.app.control_store import ControlStore
from services.control_plane.app.event_store import CommandStore, EventStore
from services.control_plane.app.outbox import OutboxStore
from services.control_plane.app.run_service import RunService


def test_outbox_drain_is_idempotent() -> None:
    store = OutboxStore(":memory:")
    store.enqueue("run_1", "run.started", {"mission_id": "m1"})
    store.enqueue("run_1", "run.succeeded", {"stop_reason": "model.finish"})
    delivered: list[tuple[str, str]] = []

    first = store.drain(
        lambda record: delivered.append((record.aggregate_id, record.event_type))
    )
    second = store.drain(
        lambda record: delivered.append((record.aggregate_id, record.event_type))
    )

    assert first == 2
    assert second == 0
    assert delivered == [
        ("run_1", "run.started"),
        ("run_1", "run.succeeded"),
    ]


def test_run_transitions_write_outbox(tmp_path) -> None:
    db_path = tmp_path / "control.sqlite3"
    events = EventStore(db_path)
    commands = CommandStore(db_path)
    outbox = OutboxStore(db_path)
    control = ControlStore(events, commands, db_path, outbox=outbox)
    runs = RunService(events, commands, control, outbox=outbox)

    project = control.create_project("lab")
    mission = control.create_mission(project.project_id, "web", {})
    run = runs.start_run(mission.mission_id, "start:1")
    runs.claim(run.run_id, "agent-worker", "claim:1")
    runs.pause(run.run_id, "pause:1")

    pending = outbox.pending()
    assert [record.event_type for record in pending] == [
        "run.queued",
        "run.claimed",
        "run.paused",
    ]

    delivered: list[str] = []
    outbox.drain(lambda record: delivered.append(record.event_type))
    assert delivered == ["run.queued", "run.claimed", "run.paused"]

    events.close()
    commands.close()
    outbox.close()
    control.close()
