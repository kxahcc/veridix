from __future__ import annotations

import json
import zipfile

from services.release_service.offline_bundle import create_bundle, verify_bundle
from services.release_service.signing import (
    generate_keypair,
    install_offline,
    sign_bundle,
    verify_bundle_signature,
)


def test_bundle_signature_roundtrip_and_tamper_detection(tmp_path) -> None:
    private_key, public_key = generate_keypair()
    bundle = tmp_path / "release.zip"
    create_bundle(
        bundle,
        files={"images/web.tar": b"image-bytes", "knowledge/index.sqlite": b"index"},
        metadata={"version": "0.1.0"},
    )

    sign_bundle(bundle, private_key)
    assert verify_bundle_signature(bundle, public_key) is True

    with zipfile.ZipFile(bundle) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries["images/web.tar"] = b"tampered"
    with zipfile.ZipFile(bundle, "w") as target:
        for name, data in entries.items():
            target.writestr(name, data)
    # Signature covers the manifest; content integrity is enforced by hashes.
    assert verify_bundle_signature(bundle, public_key) is True
    ok, failures = verify_bundle(bundle)
    assert ok is False
    assert "images/web.tar" in failures


def test_install_offline_verifies_and_extracts(tmp_path) -> None:
    private_key, public_key = generate_keypair()
    bundle = tmp_path / "release.zip"
    create_bundle(
        bundle,
        files={"images/web.tar": b"image-bytes"},
        metadata={"version": "0.1.0"},
    )
    sign_bundle(bundle, private_key)
    target = tmp_path / "installed"

    result = install_offline(bundle, target, public_key)

    assert result["files"] == ["images/web.tar"]
    assert (target / "images" / "web.tar").read_bytes() == b"image-bytes"
    metadata = json.loads(result["manifest"])["metadata"]
    assert metadata["version"] == "0.1.0"
