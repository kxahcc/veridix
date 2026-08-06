from __future__ import annotations

from services.control_plane.app.artifact_store import ArtifactStore
from services.control_plane.app.bundle import export_bundle, import_bundle
from services.control_plane.app.contracts import AgentEvent
from services.control_plane.app.event_store import EventStore
from services.control_plane.app.projection import project_run
from services.control_plane.app.snapshot_store import SnapshotStore


def test_bundle_roundtrip_and_idempotent_import(tmp_path) -> None:
    source_events = EventStore(tmp_path / "source.sqlite3")
    source_events.append(
        AgentEvent(
            event_id="evt_1",
            event_type="run.started",
            stream_id="run_1",
            run_id="run_1",
            actor="control",
        )
    )
    source_events.append(
        AgentEvent(
            event_id="evt_2",
            event_type="run.succeeded",
            stream_id="run_1",
            run_id="run_1",
            actor="agent-worker",
            payload={"stop_reason": "model.finish"},
        )
    )
    source_artifacts = ArtifactStore(tmp_path / "source-artifacts")
    source_artifacts.put(b"proof-body", content_type="text/plain")
    source_snapshots = SnapshotStore(tmp_path / "source-snapshots.sqlite3")
    source_snapshots.save(
        "behavior",
        "behavior_1",
        "0.1.0",
        {"kernel": "native", "provider": "fixture"},
    )
    bundle_path = tmp_path / "project.bundle.zip"

    summary = export_bundle(
        bundle_path,
        events=source_events,
        artifacts=source_artifacts,
        snapshots=source_snapshots,
    )
    assert summary.events == 2
    assert summary.artifacts == 1
    assert summary.snapshots == 1

    target_events = EventStore(tmp_path / "target.sqlite3")
    target_artifacts = ArtifactStore(tmp_path / "target-artifacts")
    target_snapshots = SnapshotStore(tmp_path / "target-snapshots.sqlite3")

    imported = import_bundle(
        bundle_path,
        events=target_events,
        artifacts=target_artifacts,
        snapshots=target_snapshots,
    )
    assert imported.events == 2
    assert imported.artifacts == 1
    assert imported.snapshots == 1

    replayed = target_events.replay("run_1")
    projection = project_run(replayed)
    assert projection["status"] == "succeeded"
    assert projection["event_count"] == 2
    artifact_id = target_artifacts.files()[0][0]
    assert target_artifacts.get(artifact_id) == b"proof-body"
    behavior = target_snapshots.load("behavior", "behavior_1")
    assert behavior is not None
    assert behavior.payload["provider"] == "fixture"

    second_import = import_bundle(
        bundle_path,
        events=target_events,
        artifacts=target_artifacts,
        snapshots=target_snapshots,
    )
    assert second_import.duplicates_skipped == 2
    assert len(target_events.replay("run_1")) == 2
