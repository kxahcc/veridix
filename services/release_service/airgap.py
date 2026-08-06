from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .offline_bundle import create_bundle, verify_bundle
from .policy import LicensePolicy, enforce_sbom_policy
from .signing import install_offline, sign_bundle, verify_bundle_signature


def assemble_airgap_bundle(
    out_path: str | Path,
    *,
    images: dict[str, bytes],
    knowledge_index: bytes,
    sbom: dict,
    versions: dict,
    private_key_hex: str,
    offline_manifest: dict | None = None,
    tools_tar: bytes | None = None,
    desktop_zip: bytes | None = None,
) -> dict:
    files: dict[str, bytes] = {
        f"images/{name}.tar": data for name, data in images.items()
    }
    files["knowledge/index.sqlite"] = knowledge_index
    files["sbom.json"] = json.dumps(sbom).encode("utf-8")
    files["versions.json"] = json.dumps(versions, indent=2).encode("utf-8")
    if offline_manifest is not None:
        from .offline_components import offline_manifest_bytes

        files["offline-deps.json"] = offline_manifest_bytes(offline_manifest)
    image_names = list(images)
    if tools_tar is not None:
        files["images/veridix-tools.tar.gz"] = tools_tar
        image_names.append("veridix-tools")
    if desktop_zip is not None:
        files["desktop/veridix-desktop.zip"] = desktop_zip
    create_bundle(
        out_path,
        files=files,
        metadata={"kind": "airgap", "images": image_names},
    )
    sign_bundle(out_path, private_key_hex)
    return {"bundle": str(out_path), "images": image_names}


def install_airgap(
    bundle_path: str | Path,
    target_dir: str | Path,
    public_key_hex: str,
    policy: LicensePolicy,
) -> dict:
    ok, failures = verify_bundle(bundle_path)
    if not ok:
        raise ValueError(f"airgap bundle hash verification failed: {failures}")
    if not verify_bundle_signature(bundle_path, public_key_hex):
        raise ValueError("airgap bundle signature verification failed")
    with zipfile.ZipFile(bundle_path, "r") as bundle:
        sbom = json.loads(bundle.read("sbom.json"))
        offline_components = 0
        if "offline-deps.json" in bundle.namelist():
            offline = json.loads(bundle.read("offline-deps.json"))
            offline_components = len(offline.get("components", []))
    enforce_sbom_policy(sbom, policy)
    result = install_offline(bundle_path, target_dir, public_key_hex)
    return {
        "files": result["files"],
        "images": [name for name in result["files"] if name.startswith("images/")],
        "offline_components": offline_components,
        "tools_tar": (
            str(target_dir / "images" / "veridix-tools.tar.gz")
            if (target_dir / "images" / "veridix-tools.tar.gz").exists()
            else None
        ),
        "desktop_zip": (
            str(target_dir / "desktop" / "veridix-desktop.zip")
            if (
                target_dir
                / "desktop"
                / "veridix-desktop.zip"
            ).exists()
            else None
        ),
    }
