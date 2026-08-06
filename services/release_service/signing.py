from __future__ import annotations

import base64
import shutil
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from .offline_bundle import verify_bundle


def generate_keypair() -> tuple[str, str]:
    private = ed25519.Ed25519PrivateKey.generate()
    return (
        private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ).hex(),
        private.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex(),
    )


def sign_bytes(data: bytes, private_key_hex: str) -> str:
    private = ed25519.Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(private_key_hex)
    )
    return base64.b64encode(private.sign(data)).decode("ascii")


def verify_bytes(data: bytes, signature: str, public_key_hex: str) -> bool:
    public = ed25519.Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(public_key_hex)
    )
    try:
        public.verify(base64.b64decode(signature), data)
        return True
    except (InvalidSignature, ValueError):
        return False


def sign_bundle(path: str | Path, private_key_hex: str) -> str:
    with zipfile.ZipFile(path, "r") as bundle:
        if "manifest.json" not in bundle.namelist():
            raise ValueError("bundle has no manifest.json; create the bundle first")
        manifest = bundle.read("manifest.json")
    signature = sign_bytes(manifest, private_key_hex)
    Path(path).with_name("bundle.sig").write_text(signature, encoding="utf-8")
    return signature


def verify_bundle_signature(path: str | Path, public_key_hex: str) -> bool:
    sig_path = Path(path).with_name("bundle.sig")
    try:
        with zipfile.ZipFile(path, "r") as bundle:
            if "manifest.json" not in bundle.namelist():
                return False
            manifest = bundle.read("manifest.json")
        if not sig_path.exists():
            return False
        return verify_bytes(manifest, sig_path.read_text(encoding="utf-8"), public_key_hex)
    except (zipfile.BadZipFile, OSError):
        return False


def install_offline(
    bundle_path: str | Path,
    target_dir: str | Path,
    public_key_hex: str,
) -> dict:
    ok, failures = verify_bundle(bundle_path)
    if not ok:
        raise ValueError(f"bundle hash verification failed: {failures}")
    if not verify_bundle_signature(bundle_path, public_key_hex):
        raise ValueError("bundle signature verification failed")
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    with zipfile.ZipFile(bundle_path, "r") as bundle:
        manifest = bundle.read("manifest.json")
        for name in bundle.namelist():
            if name in ("manifest.json", "bundle.sig"):
                continue
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(bundle.read(name))
            installed.append(name)
    return {"files": installed, "manifest": manifest.decode("utf-8")}
