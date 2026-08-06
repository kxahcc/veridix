from __future__ import annotations

from services.release_service.image_signing import (
    SignedImageRecord,
    load_signed_image,
    save_signed_image,
    sign_image_manifest,
    verify_image_manifest,
)
from services.release_service.signing import generate_keypair


def test_image_manifest_sign_verify_and_persist(tmp_path) -> None:
    private_key, public_key = generate_keypair()
    record = sign_image_manifest(
        "sha256:abc123",
        private_key,
        signer="veridix-release",
    )

    assert verify_image_manifest(record, public_key) is True
    assert verify_image_manifest(
        SignedImageRecord(
            image_digest="sha256:tampered",
            signature=record.signature,
            signer=record.signer,
        ),
        public_key,
    ) is False

    path = tmp_path / "signed-image.json"
    save_signed_image(record, path)
    loaded = load_signed_image(path)
    assert loaded.image_digest == "sha256:abc123"
    assert verify_image_manifest(loaded, public_key) is True
