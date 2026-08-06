from __future__ import annotations

import json

from services.release_service.airgap import assemble_airgap_bundle, install_airgap
from services.release_service.offline_components import (
    build_offline_manifest,
    parse_npm_lock,
    parse_python_lock,
)
from services.release_service.policy import LicensePolicy
from services.release_service.signing import generate_keypair


def test_parse_npm_lock_includes_scoped_packages() -> None:
    lock = {
        "packages": {
            "": {"name": "root", "version": "1.0.0"},
            "node_modules/@scope/pkg": {
                "name": "@scope/pkg",
                "version": "1.2.3",
                "integrity": "sha512-abc",
                "resolved": "https://registry.npmjs.org/@scope/pkg/-/pkg-1.2.3.tgz",
            },
            "node_modules/lodash": {
                "version": "4.17.21",
                "integrity": "sha512-def",
            },
        }
    }

    components = parse_npm_lock(lock)

    assert [item.name for item in components] == ["@scope/pkg", "lodash"]
    assert components[0].integrity == "sha512-abc"
    assert components[0].source.startswith("https://registry.npmjs.org")
    assert components[1].name == "lodash"


def test_parse_python_lock_with_hashes() -> None:
    text = (
        "fastapi==0.115.0\n"
        "  --hash=sha256:aaa\n"
        "  --hash=sha256:bbb\n"
        "pydantic==2.8.0\n"
        "  --hash=sha256:ccc\n"
    )

    components = parse_python_lock(text)

    assert [item.name for item in components] == ["fastapi", "pydantic"]
    assert components[0].integrity == "sha256:aaa"
    assert components[1].integrity == "sha256:ccc"


def test_offline_manifest_builds_sorted_components() -> None:
    manifest = build_offline_manifest(
        npm_lock={
            "packages": {
                "node_modules/z": {"version": "2.0.0"},
                "node_modules/a": {"name": "a", "version": "1.0.0"},
            }
        },
        python_lock="b==2.0.0\n",
    )

    assert [item["name"] for item in manifest["components"]] == ["a", "z", "b"]
    assert manifest["schemaVersion"] == 1


def test_airgap_bundle_carries_offline_manifest(tmp_path) -> None:
    private_key, public_key = generate_keypair()
    bundle = tmp_path / "airgap-offline.zip"
    assemble_airgap_bundle(
        bundle,
        images={"web": b"image-tar"},
        knowledge_index=b"index",
        sbom={
            "components": [
                {
                    "type": "library",
                    "name": "pypi:veridix-runtime",
                    "licenses": [{"license": {"name": "Apache-2.0"}}],
                }
            ]
        },
        versions={},
        private_key_hex=private_key,
        offline_manifest=build_offline_manifest(
            python_lock="fastapi==0.115.0\n  --hash=sha256:aaa\n"
        ),
    )

    result = install_airgap(
        bundle,
        tmp_path / "installed",
        public_key,
        LicensePolicy(allowed=("Apache-2.0",)),
    )

    assert result["offline_components"] == 1
    offline = json.loads(
        (tmp_path / "installed" / "offline-deps.json").read_text(encoding="utf-8")
    )
    assert offline["components"][0]["name"] == "fastapi"
