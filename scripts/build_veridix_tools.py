#!/usr/bin/env python
"""Build the project-specific lightweight security tools image.

Unlike the inherited veridix-tools offline pack, this image starts from
debian:bookworm-slim and only installs tools the Veridix runner actually
uses. It is intentionally separate from model/embedding/rerank runtime.

Usage:
  python scripts/build_veridix_tools.py \
    --out benchmarks/results/veridix-tools-image-2026-08-04.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = (
    "nuclei",
    "fscan",
    "nikto",
    "subfinder",
    "httpx",
    "naabu",
    "enum4linux",
)
ASSET_FILES = {
    "nuclei": "nuclei.zip",
    "fscan": "fscan",
    "nikto": "nikto.tar.gz",
    "subfinder": "subfinder",
    "httpx": "httpx",
    "naabu": "naabu",
    "enum4linux": "enum4linux",
}


def _digest_part(value: str) -> str:
    return value.rsplit("@", 1)[-1]


def _update_image_manifests(report: dict[str, object]) -> None:
    images_path = ROOT / "deploy" / "manifests" / "images.json"
    versions_path = ROOT / "deploy" / "manifests" / "versions.json"
    code_pack_path = ROOT / "deploy" / "toolpacks" / "code.json"
    if not images_path.exists():
        return
    images = json.loads(images_path.read_text(encoding="utf-8"))
    versions = (
        json.loads(versions_path.read_text(encoding="utf-8"))
        if versions_path.exists()
        else {}
    )
    entries = {
        "veridix-tools-dev": (report.get("digest"), report.get("size")),
        "veridix-tools-full": (
            report.get("full_digest"),
            report.get("full_size"),
        ),
        "veridix-tools-code-lite": (
            report.get("code_lite_digest"),
            report.get("code_lite_size"),
        ),
    }
    for name, (digest, size) in entries.items():
        if not digest:
            continue
        digest = _digest_part(str(digest))
        image = images["images"].setdefault(name, {})
        image["digest"] = digest
        if size:
            image["size"] = int(size)
        version_digests = (
            versions.setdefault("container", {}).setdefault(
                "imageDigests",
                {},
            )
        )
        version_digests[name] = digest
    images_path.write_text(
        json.dumps(images, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    if versions:
        versions_path.write_text(
            json.dumps(versions, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
    if report.get("code_lite_digest") and code_pack_path.exists():
        code_pack = json.loads(code_pack_path.read_text(encoding="utf-8"))
        code_pack["digest"] = _digest_part(
            str(report["code_lite_digest"])
        )
        code_pack_path.write_text(
            json.dumps(code_pack, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="veridix-tools:dev")
    parser.add_argument(
        "--base-image",
        default="debian:bookworm-slim",
        help="base image; use a domestic mirror when Docker Hub is unreachable",
    )
    parser.add_argument(
        "--with-metasploit",
        action="store_true",
        help="also install Metasploit from the pre-downloaded deb",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="rebuild without Docker layer cache",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="also build veridix-tools:full with metasploit/wpscan and AD/cloud/host tooling",
    )
    parser.add_argument(
        "--code",
        action="store_true",
        help="also build veridix-tools:code with semgrep/trivy/syft/grype/codeql",
    )
    parser.add_argument(
        "--code-lite",
        action="store_true",
        help="also build the lightweight veridix-tools:code-lite image",
    )
    parser.add_argument(
        "--codeql-url",
        default=(
            "https://github.com/github/codeql-action/releases/latest/"
            "download/codeql-bundle-linux64.tar.gz"
        ),
    )
    parser.add_argument(
        "--out",
        default=str(
            ROOT
            / "benchmarks"
            / "results"
            / f"veridix-tools-image-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        ),
    )
    args = parser.parse_args()

    report: dict = {
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "suite": "veridix_tools_image",
        "tag": args.tag,
        "base": "debian:bookworm-slim",
        "status": "failed",
        "steps": [],
    }

    for asset in ASSETS:
        target = (
            ROOT
            / "deploy"
            / "container"
            / "veridix-tools"
            / "downloads"
            / ASSET_FILES[asset]
        )
        if target.exists():
            result = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=f"exists {target.name}",
                stderr="",
            )
        else:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "fetch_tool_binaries.py"),
                    "--name",
                    asset,
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        report["steps"].append(
            {
                "name": f"asset:{asset}",
                "status": "passed" if result.returncode == 0 else "failed",
                "detail": (result.stdout or result.stderr)[-200:],
            }
        )
        if result.returncode != 0:
            report["status"] = "failed"
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(report, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            print(json.dumps(report, ensure_ascii=True, indent=2))
            return 1

    build_base = not (
        args.code_lite and not args.full and not args.code
    )
    if build_base:
        build_cmd = [
            "docker",
            "build",
            "-t",
            args.tag,
            "-f",
            str(
                ROOT
                / "deploy"
                / "container"
                / "veridix-tools"
                / "Dockerfile"
            ),
            "--build-arg",
            f"WITH_METASPLOIT={'1' if args.with_metasploit else '0'}",
            "--build-arg",
            f"BASE_IMAGE={args.base_image}",
            str(ROOT),
        ]
        if args.no_cache:
            build_cmd.insert(2, "--no-cache")
        build = subprocess.run(
            build_cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        report["steps"].append(
            {
                "name": "docker_build",
                "status": "passed" if build.returncode == 0 else "failed",
                "detail": (build.stdout or build.stderr)[-300:],
            }
        )
        if build.returncode != 0:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(report, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            print(json.dumps(report, ensure_ascii=True, indent=2))
            return 1

        inspect = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                args.tag,
                "--format",
                "{{.Id}} {{.Size}} {{index .RepoDigests 0}}",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if inspect.returncode == 0:
            image_id, size, digest = inspect.stdout.strip().split()
            report["image_id"] = image_id
            report["size"] = int(size)
            report["digest"] = digest

    if args.code:
        code_tag = "veridix-tools:code"
        code_cmd = [
            "docker",
            "build",
            "-t",
            code_tag,
            "-f",
            str(
                ROOT
                / "deploy"
                / "container"
                / "veridix-tools"
                / "Dockerfile.code"
            ),
            "--build-arg",
            "BASE_IMAGE=veridix-tools:full",
            "--build-arg",
            f"CODEQL_URL={args.codeql_url}",
            str(ROOT),
        ]
        if args.no_cache:
            code_cmd.insert(2, "--no-cache")
        code = subprocess.run(
            code_cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2400,
        )
        report["steps"].append(
            {
                "name": "docker_build_code",
                "status": (
                    "passed" if code.returncode == 0 else "failed"
                ),
                "detail": (code.stdout or code.stderr)[-300:],
            }
        )
        if code.returncode != 0:
            report["status"] = "failed"
        else:
            inspect_code = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    code_tag,
                    "--format",
                    "{{.Id}} {{.Size}} {{index .RepoDigests 0}}",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if inspect_code.returncode == 0:
                image_id, size, digest = (
                    inspect_code.stdout.strip().split()
                )
                report["code_image_id"] = image_id
                report["code_size"] = int(size)
                report["code_digest"] = digest
                report["tag"] = code_tag

    if args.code_lite:
        code_lite_tag = "veridix-tools:code-lite"
        code_lite_cmd = [
            "docker",
            "build",
            "-t",
            code_lite_tag,
            "-f",
            str(
                ROOT
                / "deploy"
                / "container"
                / "veridix-tools"
                / "Dockerfile.code-lite"
            ),
            "--build-arg",
            f"BASE_IMAGE={args.base_image}",
            str(ROOT),
        ]
        if args.no_cache:
            code_lite_cmd.insert(2, "--no-cache")
        code_lite = subprocess.run(
            code_lite_cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        report["steps"].append(
            {
                "name": "docker_build_code_lite",
                "status": (
                    "passed" if code_lite.returncode == 0 else "failed"
                ),
                "detail": (code_lite.stdout or code_lite.stderr)[-300:],
            }
        )
        if code_lite.returncode != 0:
            report["status"] = "failed"
        else:
            inspect_code_lite = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    code_lite_tag,
                    "--format",
                    "{{.Id}} {{.Size}} {{index .RepoDigests 0}}",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if inspect_code_lite.returncode == 0:
                image_id, size, digest = (
                    inspect_code_lite.stdout.strip().split()
                )
                report["code_lite_image_id"] = image_id
                report["code_lite_size"] = int(size)
                report["code_lite_digest"] = digest
                report["tag"] = code_lite_tag

    if args.full:
        full_tag = "veridix-tools:full"
        full_cmd = [
            "docker",
            "build",
            "-t",
            full_tag,
            "-f",
            str(
                ROOT
                / "deploy"
                / "container"
                / "veridix-tools"
                / "Dockerfile.full"
            ),
            "--build-arg",
            "BASE_IMAGE=veridix-tools:dev",
            str(ROOT),
        ]
        if args.no_cache:
            full_cmd.insert(2, "--no-cache")
        full = subprocess.run(
            full_cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        report["steps"].append(
            {
                "name": "docker_build_full",
                "status": (
                    "passed" if full.returncode == 0 else "failed"
                ),
                "detail": (full.stdout or full.stderr)[-300:],
            }
        )
        if full.returncode != 0:
            report["status"] = "failed"
        else:
            inspect_full = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    full_tag,
                    "--format",
                    "{{.Id}} {{.Size}} {{index .RepoDigests 0}}",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if inspect_full.returncode == 0:
                image_id, size, digest = (
                    inspect_full.stdout.strip().split()
                )
                report["full_image_id"] = image_id
                report["full_size"] = int(size)
                report["full_digest"] = digest
                report["tag"] = full_tag

    report["status"] = (
        "passed"
        if all(
            step["status"] == "passed"
            for step in report["steps"]
        )
        else "failed"
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if report["status"] == "passed":
        _update_image_manifests(report)
    out.write_text(
        json.dumps(report, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

