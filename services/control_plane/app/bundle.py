from __future__ import annotations

import json
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .artifact_store import ARTIFACT_ID_PATTERN, ArtifactStore
from .contracts import AgentEvent
from .event_store import EventStore
from .snapshot_store import SnapshotStore


@dataclass(frozen=True)
class BundleSummary:
    events: int
    artifacts: int
    snapshots: int
    duplicates_skipped: int


def export_bundle(
    out_path: str | Path,
    *,
    events: EventStore,
    artifacts: ArtifactStore,
    snapshots: SnapshotStore | None = None,
) -> BundleSummary:
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        event_count = 0
        for stream_id in events.stream_ids():
            rows = [
                event.model_dump(mode="json")
                for event in events.replay(stream_id)
            ]
            bundle.writestr(f"events/{stream_id}.jsonl", lines(rows))
            event_count += len(rows)

        manifest: list[dict] = []
        for artifact_id, path in artifacts.files():
            bundle.write(path, f"artifacts/{path.name}")
            manifest.append(
                {
                    "artifact_id": artifact_id,
                    "size": path.stat().st_size,
                }
            )
        bundle.writestr("artifacts/manifest.json", json.dumps(manifest))

        snapshot_count = 0
        if snapshots is not None:
            rows = [record.model_dump(mode="json") for record in snapshots.all()]
            bundle.writestr("snapshots.jsonl", lines(rows))
            snapshot_count = len(rows)

    return BundleSummary(
        events=event_count,
        artifacts=len(manifest),
        snapshots=snapshot_count,
        duplicates_skipped=0,
    )


def import_bundle(
    in_path: str | Path,
    *,
    events: EventStore,
    artifacts: ArtifactStore,
    snapshots: SnapshotStore | None = None,
) -> BundleSummary:
    event_count = 0
    artifact_count = 0
    snapshot_count = 0
    duplicates_skipped = 0
    with zipfile.ZipFile(in_path, "r") as bundle:
        for name in bundle.namelist():
            if name.startswith("events/") and name.endswith(".jsonl"):
                for line in bundle.read(name).decode("utf-8").splitlines():
                    payload = json.loads(line)
                    event = AgentEvent(**payload)
                    try:
                        events.append(event)
                        event_count += 1
                    except sqlite3.IntegrityError:
                        duplicates_skipped += 1
            elif name.startswith("artifacts/") and ARTIFACT_ID_PATTERN.match(name.split("/")[-1]):
                data = bundle.read(name)
                artifacts.put(data)
                artifact_count += 1
            elif name == "snapshots.jsonl" and snapshots is not None:
                for line in bundle.read(name).decode("utf-8").splitlines():
                    payload = json.loads(line)
                    snapshots.save(
                        payload["snapshot_type"],
                        payload["snapshot_id"],
                        payload["version"],
                        payload["payload"],
                    )
                    snapshot_count += 1
    return BundleSummary(
        events=event_count,
        artifacts=artifact_count,
        snapshots=snapshot_count,
        duplicates_skipped=duplicates_skipped,
    )


def lines(rows: list[dict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows)
