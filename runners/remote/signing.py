from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def generate_keypair() -> tuple[str, str]:
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key()
    return (
        private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ).hex(),
        public.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        ).hex(),
    )


def sign_payload(payload: dict[str, Any], private_key_hex: str) -> str:
    private = ed25519.Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(private_key_hex)
    )
    message = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return base64.b64encode(private.sign(message)).decode("ascii")


def verify_payload(payload: dict[str, Any], signature: str, public_key_hex: str) -> bool:
    public = ed25519.Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(public_key_hex)
    )
    message = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    try:
        public.verify(base64.b64decode(signature), message)
        return True
    except (InvalidSignature, ValueError):
        return False
