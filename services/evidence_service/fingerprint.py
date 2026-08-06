from __future__ import annotations

import hashlib
import json


def finding_fingerprint(
    *,
    target_ref: str,
    vuln_category: str,
    endpoint: str,
    param: str = "",
) -> str:
    canonical = json.dumps(
        {
            "target": target_ref,
            "category": vuln_category,
            "endpoint": endpoint,
            "param": param,
        },
        sort_keys=True,
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
