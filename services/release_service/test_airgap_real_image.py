from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from services.release_service.airgap import assemble_airgap_bundle, install_airgap
from services.release_service.policy import LicensePolicy
from services.release_service.signing import generate_keypair


@pytest.mark.integration
def test_airgap_real_image_save_load(tmp_path) -> None:
    image = os.environ.get("VERIDIX_AIRGAP_IMAGE")
    if not image:
        pytest.skip("VERIDIX_AIRGAP_IMAGE is not set")
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")

    image_tar = subprocess.run(
        ["docker", "save", image],
        capture_output=True,
        check=True,
    ).stdout
    assert len(image_tar) > 0

    private_key, public_key = generate_keypair()
    bundle = tmp_path / "airgap-real.zip"
    assemble_airgap_bundle(
        bundle,
        images={"runtime": image_tar},
        knowledge_index=b"index-bytes",
        sbom={
            "components": [
                {
                    "type": "container",
                    "name": image,
                    "licenses": [{"license": {"name": "Apache-2.0"}}],
                }
            ]
        },
        versions={"container": {"image": image}},
        private_key_hex=private_key,
    )

    installed = tmp_path / "installed"
    result = install_airgap(
        bundle,
        installed,
        public_key,
        LicensePolicy(allowed=("Apache-2.0",)),
    )
    assert result["images"] == ["images/runtime.tar"]

    load = subprocess.run(
        ["docker", "load", "-i", str(installed / "images" / "runtime.tar")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Loaded image" in load.stdout or "Loaded image" in load.stderr
