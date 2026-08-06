from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from .attestation import ENFORCEMENT_MATRIX, certify, check_assurance


def platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def expected_assurance(platform: str) -> dict[str, Any]:
    matrix = ENFORCEMENT_MATRIX.get(platform.lower(), {})
    backend_controls = {
        control: "enforced" for control in matrix
    }
    attestation = certify(
        sandbox_id=f"matrix_{platform}",
        platform=platform,
        backend="matrix",
        profile="S2",
        image_digest="sha256:matrix",
        backend_controls=backend_controls,
        effective_uid=65532,
    )
    ok, missing = check_assurance(attestation)
    return {
        "platform": platform,
        "expected_s2_assurance": ok,
        "missing": missing,
    }


def build_platform_matrix(
    *,
    current_platform: str | None = None,
    real_docker: dict | None = None,
) -> dict:
    entries = [
        expected_assurance(platform)
        for platform in ("linux", "windows", "macos")
    ]
    return {
        "product": "veridix",
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "current_platform": current_platform or platform_name(),
        "entries": entries,
        "real_docker": real_docker,
    }
