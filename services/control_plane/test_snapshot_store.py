from __future__ import annotations

from services.control_plane.app.snapshot_store import SnapshotStore, canonical_hash


def test_snapshot_save_load_and_latest(tmp_path) -> None:
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    first = store.save(
        "config",
        "config_1",
        "1",
        {"profile": "desktop", "security": {"network": "proxy"}},
    )
    second = store.save(
        "config",
        "config_2",
        "1",
        {"profile": "lab", "security": {"network": "proxy"}},
    )

    loaded = store.load("config", "config_1")
    latest = store.latest("config")

    assert loaded is not None
    assert loaded.hash == canonical_hash(loaded.payload)
    assert latest is not None
    assert latest.snapshot_id == "config_2"
    assert first.hash != second.hash
    store.close()
