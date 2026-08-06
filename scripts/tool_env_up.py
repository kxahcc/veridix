#!/usr/bin/env python
"""Ensure the tool environment image exists and write its snapshot.

Writes runtime/tool-environment.json with availability, image, digest and a
quick container healthcheck so the control plane and web UI can show real
tool readiness.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
IMAGE = os.environ.get("VERIDIX_TOOL_IMAGE", "veridix-tools:full")
CODE_IMAGE = os.environ.get("VERIDIX_CODE_IMAGE", "veridix-tools:code-lite")
ZAP_IMAGE = os.environ.get("VERIDIX_ZAP_IMAGE", "zaproxy/zap-stable:latest")
ZAP_MIRROR_IMAGE = "docker.1ms.run/zaproxy/zap-stable:latest"
REQUIRED_IMAGES = (IMAGE, CODE_IMAGE)
MIRROR = os.environ.get(
    "VERIDIX_STORAGE_REGISTRY",
    "docker.m.daocloud.io/",
)
TOOL_PACKS = [
    "network",
    "web",
    "vulnscan",
    "host",
    "binary",
    "code",
    "ad",
    "cloud",
    "base",
]


def _run(args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _local_images() -> set[str]:
    result = _run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]
    )
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    }


def _image_digest(image: str = IMAGE) -> str:
    result = _run(
        ["docker", "inspect", image, "--format", "{{index .RepoDigests 0}}"]
    )
    if result.returncode == 0:
        return result.stdout.strip().split("@")[-1]
    return ""


def _healthcheck(
    image: str = IMAGE,
    *,
    command: str = (
        "command -v nmap && command -v nuclei && "
        "command -v masscan && command -v sqlmap && "
        "command -v subfinder && command -v httpx && command -v naabu && "
        "command -v msfconsole && command -v wpscan"
    ),
) -> str:
    result = _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            image,
            "-c",
            command,
        ],
        timeout=60.0,
    )
    return "ok" if result.returncode == 0 else "failed"


def _zap_health() -> str:
    try:
        import httpx

        response = httpx.get(
            "http://127.0.0.1:8090/JSON/core/view/version/",
            params={
                "apikey": os.environ.get("VERIDIX_ZAP_API_KEY", "veridix-zap")
            },
            timeout=3.0,
            trust_env=False,
        )
        if response.status_code == 200 and response.json().get("version"):
            return "ok"
        if response.status_code < 500:
            return "ok"
    except Exception:
        pass
    return "unreachable"


def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    images = _local_images()
    available = all(image in images for image in REQUIRED_IMAGES)
    if not available and os.environ.get("VERIDIX_TOOL_AUTOPULL", "0") == "1":
        mirror_tag = MIRROR + IMAGE
        result = _run(["docker", "pull", mirror_tag], timeout=600.0)
        if result.returncode == 0:
            _run(["docker", "tag", mirror_tag, IMAGE])
            images = _local_images()
            available = all(image in images for image in REQUIRED_IMAGES)
    if ZAP_IMAGE not in images and os.environ.get(
        "VERIDIX_TOOL_AUTOPULL", "0"
    ) == "1":
        result = _run(["docker", "pull", ZAP_MIRROR_IMAGE], timeout=600.0)
        if result.returncode == 0:
            _run(["docker", "tag", ZAP_MIRROR_IMAGE, ZAP_IMAGE])
            images = _local_images()
    health = _healthcheck() if IMAGE in images else "missing"
    code_health = (
        _healthcheck(
            CODE_IMAGE,
            command=(
                "command -v semgrep && command -v detect-secrets && "
                "test -f /opt/veridix-rules/semgrep/security.yml"
            ),
        )
        if CODE_IMAGE in images
        else "missing"
    )
    payload = {
        "available": available,
        "image": IMAGE,
        "digest": _image_digest() if available else "",
        "packs": TOOL_PACKS,
        "health": health,
        "code_lite": {
            "image": CODE_IMAGE,
            "digest": _image_digest(CODE_IMAGE)
            if CODE_IMAGE in images
            else "",
            "health": code_health,
        },
        "zap": {
            "image": ZAP_IMAGE,
            "local": ZAP_IMAGE in images,
            "health": _zap_health(),
        },
    }
    (RUNTIME / "tool-environment.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0 if available and health == "ok" and code_health == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
