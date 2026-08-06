from __future__ import annotations

import pytest

from services.release_service.airgap import assemble_airgap_bundle, install_airgap
from services.release_service.policy import LicensePolicy
from services.release_service.signing import generate_keypair


SBOM = {
    "components": [
        {
            "type": "library",
            "name": "pypi:veridix-runtime",
            "licenses": [{"license": {"name": "Apache-2.0"}}],
        }
    ]
}


def test_airgap_assemble_and_install(tmp_path) -> None:
    private_key, public_key = generate_keypair()
    bundle = tmp_path / "airgap.zip"
    assembled = assemble_airgap_bundle(
        bundle,
        images={"web": b"image-tar"},
        knowledge_index=b"index-bytes",
        sbom=SBOM,
        versions={"runtime": {"python": "3.13"}},
        private_key_hex=private_key,
        tools_tar=b"tools-gzip",
        desktop_zip=b"desktop-zip",
    )

    result = install_airgap(
        bundle,
        tmp_path / "installed",
        public_key,
        LicensePolicy(allowed=("Apache-2.0",)),
    )

    assert assembled["images"] == ["web", "veridix-tools"]
    assert "veridix-tools" in assembled["images"]
    assert result["images"] == [
        "images/web.tar",
        "images/veridix-tools.tar.gz",
    ]
    assert result["tools_tar"] is not None
    assert (
        tmp_path / "installed" / "images" / "veridix-tools.tar.gz"
    ).read_bytes() == b"tools-gzip"
    assert result["desktop_zip"] is not None
    assert (
        tmp_path
        / "installed"
        / "desktop"
        / "veridix-desktop.zip"
    ).read_bytes() == b"desktop-zip"
    assert (tmp_path / "installed" / "images" / "web.tar").read_bytes() == b"image-tar"
    assert (tmp_path / "installed" / "knowledge" / "index.sqlite").read_bytes() == b"index-bytes"


def test_airgap_install_rejects_blocked_license(tmp_path) -> None:
    private_key, public_key = generate_keypair()
    bundle = tmp_path / "airgap.zip"
    assemble_airgap_bundle(
        bundle,
        images={"web": b"image-tar"},
        knowledge_index=b"index",
        sbom={
            "components": [
                {
                    "type": "library",
                    "name": "pypi:blocked",
                    "licenses": [{"license": {"name": "Proprietary"}}],
                }
            ]
        },
        versions={},
        private_key_hex=private_key,
    )

    with pytest.raises(ValueError, match="sbom policy blocked"):
        install_airgap(
            bundle,
            tmp_path / "installed",
            public_key,
            LicensePolicy(allowed=("Apache-2.0",)),
        )
