from __future__ import annotations

import json
import pytest
from pathlib import Path

from services.release_service.migrations import MigrationStore
from services.release_service.offline_bundle import create_bundle, verify_bundle
from services.release_service.profiles import load_all
from services.release_service.sbom import generate_sbom_from_manifest
from services.release_service.upgrade import check_upgrade_plan, create_backup
from services.release_service.v2_import import V2LicenseNotRecorded, import_v2_snapshot


def test_profiles_load_all() -> None:
    profiles = load_all("deploy/profiles")

    assert {profile.name for profile in profiles} == {
        "desktop",
        "lab",
        "server",
        "airgap",
    }


def test_sbom_generated_from_manifest() -> None:
    sbom = generate_sbom_from_manifest("deploy/manifests/versions.json")

    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["metadata"]["component"]["name"] == "veridix"
    assert any("fastapi" in component["name"] for component in sbom["components"])
    assert any(
        component["type"] == "container"
        and component["name"] == "veridix-tools-full"
        for component in sbom["components"]
    )


def test_python_lock_manifest_is_pinned() -> None:
    lock = Path("deploy/manifests/python.lock")

    assert lock.exists()
    lines = [
        line
        for line in lock.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert all("==" in line for line in lines)
    assert any(line.startswith("fastapi==") for line in lines)


def test_sbom_includes_image_metadata(tmp_path) -> None:
    manifest = tmp_path / "versions.json"
    manifest.write_text(
        json.dumps({"container": {"imageDigests": {"img": "sha256:x"}}}),
        encoding="utf-8",
    )
    (tmp_path / "images.json").write_text(
        json.dumps(
            {
                "images": {
                    "img": {
                        "digest": "sha256:x",
                        "size": 123,
                        "os": "linux",
                        "architecture": "amd64",
                        "labels": {},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    sbom = generate_sbom_from_manifest(manifest)

    container = next(
        component
        for component in sbom["components"]
        if component["type"] == "container"
    )
    assert container["version"] == "sha256:x"
    assert container["description"] == "linux/amd64"


def test_offline_bundle_roundtrip_and_tamper_detection(tmp_path) -> None:
    bundle_path = tmp_path / "bundle.zip"
    create_bundle(
        bundle_path,
        files={
            "images/web.tar": b"image-bytes",
            "knowledge/index.sqlite": b"index-bytes",
        },
        metadata={"version": "0.1.0"},
    )

    ok, failures = verify_bundle(bundle_path)
    assert ok is True
    assert failures == []

    import zipfile

    with zipfile.ZipFile(bundle_path) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries["images/web.tar"] = b"tampered"
    with zipfile.ZipFile(bundle_path, "w") as target:
        for name, data in entries.items():
            target.writestr(name, data)

    ok, failures = verify_bundle(bundle_path)
    assert ok is False
    assert "images/web.tar" in failures


def test_migrations_apply_and_rollback(tmp_path) -> None:
    db = tmp_path / "state.sqlite3"
    store = MigrationStore(db)
    applied: list[str] = []

    record = store.apply("m1", "2", "add events index", lambda: applied.append("up"))
    assert record.version == "2"
    assert applied == ["up"]

    store.rollback("m1", lambda: applied.append("down"))
    assert applied == ["up", "down"]
    assert store.list_applied() == []


def test_upgrade_plan_and_backup(tmp_path) -> None:
    db = tmp_path / "state.sqlite3"
    db.write_bytes(b"state")

    plan = check_upgrade_plan(
        db_path=db,
        required_disk_mb=1,
        backups_writable=True,
    )
    assert plan.upgrade_ok is True

    missing = check_upgrade_plan(
        db_path=tmp_path / "missing.sqlite3",
        required_disk_mb=1,
        backups_writable=True,
    )
    assert missing.upgrade_ok is False
    assert "database missing" in missing.reasons

    backup = create_backup(db, tmp_path / "backup.sqlite3")
    assert (tmp_path / "backup.sqlite3").read_bytes() == b"state"


def test_v2_import_requires_recorded_license() -> None:
    with pytest.raises(V2LicenseNotRecorded):
        import_v2_snapshot(
            {"projects": [], "runs": []},
            license_recorded=False,
            source_commit="a2a19b2",
        )

    decision = import_v2_snapshot(
        {"projects": [{"id": "p1"}], "runs": []},
        license_recorded=True,
        source_commit="a2a19b2",
    )
    assert decision.accepted is True
    assert decision.imported["read_only"] is True
    assert decision.imported["source_commit"] == "a2a19b2"
