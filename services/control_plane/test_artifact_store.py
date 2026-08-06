from __future__ import annotations

import hashlib

import pytest

from services.control_plane.app.artifact_store import (
    ArtifactQuotaExceeded,
    ArtifactStore,
)


def test_put_get_verify_content_addressed(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    data = b"evidence-body"
    digest = hashlib.sha256(data).hexdigest()

    first = store.put(data, content_type="text/plain")
    second = store.put(data, content_type="text/plain")

    assert first.artifact_id == digest
    assert first.artifact_id == second.artifact_id
    assert store.get(digest) == data
    assert store.verify(digest) is True
    assert len(store.files()) == 1
    assert store.used_bytes() == len(data)
    assert (
        tmp_path / "artifacts" / "sha256" / digest[:2] / digest[2:4] / digest
    ).exists()


def test_get_detects_hash_mismatch(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    digest = hashlib.sha256(b"original").hexdigest()
    path = (
        tmp_path
        / "artifacts"
        / "sha256"
        / digest[:2]
        / digest[2:4]
        / digest
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(b"tampered")

    assert store.verify(digest) is False
    with pytest.raises(ValueError, match="hash mismatch"):
        store.get(digest)


def test_gc_removes_unreferenced_and_keeps_referenced(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    kept = store.put(b"keep-me")
    dropped = store.put(b"drop-me")

    removed = store.gc(keep={kept.artifact_id})

    assert removed == [dropped.artifact_id]
    assert store.verify(kept.artifact_id) is True
    assert store.verify(dropped.artifact_id) is False


def test_quota_is_enforced(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts", max_bytes=10)
    store.put(b"12345")

    with pytest.raises(ArtifactQuotaExceeded):
        store.put(b"123456")


def test_interrupted_tmp_is_recovered_on_put(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    data = b"evidence-body"
    digest = hashlib.sha256(data).hexdigest()
    tmp = (
        tmp_path
        / "artifacts"
        / "sha256"
        / digest[:2]
        / digest[2:4]
        / f".{digest}.tmp"
    )
    tmp.parent.mkdir(parents=True)
    tmp.write_bytes(b"partial")

    artifact = store.put(data)

    assert artifact.artifact_id == digest
    assert store.get(digest) == data
    assert tmp.exists() is False


def test_corrupt_final_file_is_repaired_on_put(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    data = b"evidence-body"
    digest = hashlib.sha256(data).hexdigest()
    target = (
        tmp_path
        / "artifacts"
        / "sha256"
        / digest[:2]
        / digest[2:4]
        / digest
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt")

    assert store.verify(digest) is False
    store.put(data)

    assert store.get(digest) == data
    assert store.verify(digest) is True
