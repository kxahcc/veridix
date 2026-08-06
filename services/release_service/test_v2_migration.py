from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from services.release_service.migrations import MigrationStore
from services.release_service.v2_import import (
    V2ImportStore,
    V2LicenseNotRecorded,
    apply_v2_migration,
    normalize_v2_snapshot,
    rollback_v2_migration,
)


SNAPSHOT = {
    "projects": [
        {
            "id": "p1",
            "name": "legacy",
            "targets": [{"id": "t1", "url": "https://legacy.example"}],
        }
    ],
    "runs": [
        {
            "id": "r1",
            "mission_id": "m1",
            "status": "succeeded",
            "events": [{"id": "e1"}, {"id": "e2"}],
        }
    ],
}


def test_normalize_v2_snapshot_is_deterministic() -> None:
    first = normalize_v2_snapshot(SNAPSHOT, source_commit="abc")
    second = normalize_v2_snapshot(SNAPSHOT, source_commit="abc")

    assert first == second
    assert first["event_count"] == 2
    assert first["projects"][0]["name"] == "legacy"
    assert first["targets"][0]["url"] == "https://legacy.example"
    assert first["runs"][0]["status"] == "succeeded"


def test_v2_migration_apply_and_rollback(tmp_path) -> None:
    db = tmp_path / "state.sqlite3"

    record = apply_v2_migration(
        SNAPSHOT,
        db_path=db,
        license_recorded=True,
        source_commit="abc",
    )

    store = MigrationStore(db)
    assert store.list_applied()[0].id == "v2:abc"
    store.close()
    import_store = V2ImportStore(db)
    assert import_store.count() == 1
    assert import_store.get("v2:abc")["payload"]["event_count"] == 2
    import_store.close()

    rollback_v2_migration(db_path=db, migration_id="v2:abc")

    store = MigrationStore(db)
    assert store.list_applied() == []
    store.close()
    import_store = V2ImportStore(db)
    assert import_store.count() == 0
    import_store.close()


def test_v2_migration_requires_recorded_license(tmp_path) -> None:
    with pytest.raises(V2LicenseNotRecorded):
        apply_v2_migration(
            SNAPSHOT,
            db_path=tmp_path / "state.sqlite3",
            license_recorded=False,
            source_commit="abc",
        )


def test_v2_migration_cli_applies_and_rolls_back(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    snapshot = tmp_path / "v2.json"
    snapshot.write_text(json.dumps(SNAPSHOT), encoding="utf-8")
    db = tmp_path / "state.sqlite3"

    applied = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.release_service.v2_migration_cli",
            "--snapshot",
            str(snapshot),
            "--db",
            str(db),
            "--source-commit",
            "abc",
            "--license-recorded",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert applied.returncode == 0
    assert json.loads(applied.stdout)["migration_id"] == "v2:abc"

    rolled_back = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.release_service.v2_migration_cli",
            "--snapshot",
            str(snapshot),
            "--db",
            str(db),
            "--source-commit",
            "abc",
            "--license-recorded",
            "--rollback",
            "--migration-id",
            "v2:abc",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rolled_back.returncode == 0
    assert json.loads(rolled_back.stdout)["rolled_back"] == "v2:abc"
