from __future__ import annotations

import argparse
import sys
import time
import zipfile
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_DIR = (
    ROOT
    / "deploy"
    / "container"
    / "veridix-tools"
    / "downloads"
)

GITHUB_MIRRORS = (
    "https://ghfast.top/{url}",
    "https://gh-proxy.com/{url}",
    "https://ghproxy.net/{url}",
    "{url}",
)

DEFAULT_ASSETS = {
    "nuclei": {
        "filename": "nuclei.zip",
        "url": (
            "https://github.com/projectdiscovery/nuclei/releases/download/"
            "v3.11.0/nuclei_3.11.0_linux_amd64.zip"
        ),
        "mirrors": GITHUB_MIRRORS,
    },
    "fscan": {
        "filename": "fscan",
        "url": (
            "https://github.com/shadow1ng/fscan/releases/download/"
            "v2.2.0/fscan_2.2.0_linux_x64"
        ),
        "mirrors": GITHUB_MIRRORS,
    },
    "metasploit": {
        "filename": "metasploit.deb",
        "url": (
            "https://apt.metasploit.com/pool/main/m/metasploit-framework/"
            "metasploit-framework_6.5.1~20260802055644~1rapid7-1_amd64.deb"
        ),
        "mirrors": ("{url}",),
    },
    "nikto": {
        "filename": "nikto.tar.gz",
        "url": (
            "https://github.com/sullo/nikto/archive/refs/tags/2.5.0.tar.gz"
        ),
        "mirrors": GITHUB_MIRRORS,
    },
    "enum4linux": {
        "filename": "enum4linux",
        "url": (
            "https://raw.githubusercontent.com/"
            "portcullislabs/enum4linux/master/enum4linux.pl"
        ),
        "mirrors": GITHUB_MIRRORS,
    },
    "subfinder": {
        "filename": "subfinder.zip",
        "binary": "subfinder",
        "extract": True,
        "url": (
            "https://github.com/projectdiscovery/subfinder/releases/download/"
            "v2.14.0/subfinder_2.14.0_linux_amd64.zip"
        ),
        "mirrors": GITHUB_MIRRORS,
    },
    "httpx": {
        "filename": "httpx.zip",
        "binary": "httpx",
        "extract": True,
        "url": (
            "https://github.com/projectdiscovery/httpx/releases/download/"
            "v1.10.0/httpx_1.10.0_linux_amd64.zip"
        ),
        "mirrors": GITHUB_MIRRORS,
    },
    "naabu": {
        "filename": "naabu.zip",
        "binary": "naabu",
        "extract": True,
        "url": (
            "https://github.com/projectdiscovery/naabu/releases/download/"
            "v2.6.1/naabu_2.6.1_linux_amd64.zip"
        ),
        "mirrors": GITHUB_MIRRORS,
    },
}


def download_asset(
    name: str,
    *,
    retries: int = 5,
    timeout: float = 120.0,
) -> Path:
    asset = DEFAULT_ASSETS[name]
    target = DOWNLOAD_DIR / asset["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    urls = [
        template.format(url=asset["url"])
        for template in asset["mirrors"]
    ]
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        for url in urls:
            try:
                _download(url, target, timeout=timeout)
                if asset.get("extract"):
                    _extract_zip(target, asset["binary"])
                return target
            except Exception as error:
                last_error = error
                print(
                    f"[{name}] attempt {attempt} failed for {url}: {error}",
                    flush=True,
                )
        if attempt < retries:
            time.sleep(min(10 * attempt, 60))
    raise RuntimeError(
        f"failed to download {name}: {last_error}"
    ) from last_error


def _extract_zip(archive: Path, binary_name: str) -> Path:
    with zipfile.ZipFile(archive) as handle:
        member = next(
            name
            for name in handle.namelist()
            if Path(name).name == binary_name
        )
        handle.extract(member, archive.parent)
    archive.unlink()
    return archive.parent / binary_name


def _download(url: str, target: Path, *, timeout: float) -> None:
    with httpx.stream(
        "GET",
        url,
        follow_redirects=True,
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch optional tool binaries for the offline tool image"
    )
    parser.add_argument(
        "--name",
        choices=tuple(DEFAULT_ASSETS),
        action="append",
        help="asset to download; repeat for multiple, default all",
    )
    parser.add_argument("--retries", type=int, default=5)
    args = parser.parse_args()
    names = args.name or list(DEFAULT_ASSETS)
    for name in names:
        target = download_asset(name, retries=args.retries)
        print(f"{name} -> {target}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
