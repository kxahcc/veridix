#!/usr/bin/env python
"""Write SHA256SUMS, a signed release manifest, and the public key."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.release_service.signing import generate_keypair, sign_bytes, verify_bytes


def main() -> int:
    out = Path(os.environ["RELEASE_DIR"])
    tag = os.environ.get("RELEASE_VERSION", "")
    version = tag.lstrip("v")
    artifacts = sorted(
        path.name
        for path in out.iterdir()
        if path.is_file() and path.name.endswith((".zip", ".tar.gz", ".exe"))
    )
    entries: dict[str, dict] = {}
    sums: list[str] = []
    for name in artifacts:
        data = (out / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        entries[name] = {"sha256": digest, "size": len(data)}
        sums.append(f"{digest}  {name}")

    private_key, public_key = generate_keypair()
    manifest = {
        "product": "veridix",
        "version": version,
        "tag": tag,
        "commit": os.environ.get("GITHUB_SHA", ""),
        "generated_at": os.environ.get("GITHUB_RUN_ID", ""),
        "public_key": public_key,
        "artifacts": entries,
    }
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    signature = sign_bytes(payload, private_key)
    if not verify_bytes(payload, signature, public_key):
        raise SystemExit("release manifest signature verification failed")

    (out / "SHA256SUMS.txt").write_text(
        "\n".join(sums) + "\n",
        encoding="utf-8",
    )
    (out / "release-manifest.json").write_bytes(payload)
    (out / "release-manifest.sig").write_text(signature, encoding="ascii")
    (out / "veridix-public-key.txt").write_text(
        public_key + "\n",
        encoding="ascii",
    )
    print(
        json.dumps(
            {
                "manifest": str(out / "release-manifest.json"),
                "signature": str(out / "release-manifest.sig"),
                "sha256": str(out / "SHA256SUMS.txt"),
                "public_key": str(out / "veridix-public-key.txt"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
