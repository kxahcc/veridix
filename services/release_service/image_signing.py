from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .signing import sign_bytes, verify_bytes


@dataclass(frozen=True)
class SignedImageRecord:
    image_digest: str
    signature: str
    signer: str


def sign_image_manifest(
    image_digest: str,
    private_key_hex: str,
    *,
    signer: str = "veridix-release",
) -> SignedImageRecord:
    payload = json.dumps({"image_digest": image_digest}, sort_keys=True).encode("utf-8")
    return SignedImageRecord(
        image_digest=image_digest,
        signature=sign_bytes(payload, private_key_hex),
        signer=signer,
    )


def verify_image_manifest(
    record: SignedImageRecord,
    public_key_hex: str,
) -> bool:
    payload = json.dumps(
        {"image_digest": record.image_digest},
        sort_keys=True,
    ).encode("utf-8")
    return verify_bytes(payload, record.signature, public_key_hex)


def save_signed_image(
    record: SignedImageRecord,
    path: str | Path,
) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(record.__dict__, indent=2),
        encoding="utf-8",
    )


def load_signed_image(path: str | Path) -> SignedImageRecord:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SignedImageRecord(**data)
