from __future__ import annotations

import zipfile
from io import BytesIO

from .skill_package import import_skill_package


SKILL_MD = """---
name: custom-recon
version: 1.0
trigger: web_discovery
description: A deterministic custom recon skill for tests.
required_tools: [nmap.scan]
runner: container
risk_level: L2
---
# Custom Recon

Run a deterministic recon pass and record endpoints.
"""


def _zip_package() -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("custom-recon/SKILL.md", SKILL_MD)
        archive.writestr("custom-recon/references/checklist.md", "# Checklist")
    return stream.getvalue()


def test_import_skill_package_zip(tmp_path) -> None:
    result = import_skill_package(
        _zip_package(),
        target_root=tmp_path,
        filename="custom.zip",
    )
    assert result["skill_ref"] == "custom-recon"
    assert "SKILL.md" in result["files"]
    assert "references/checklist.md" in result["files"]
    assert (tmp_path / "custom-recon" / "SKILL.md").exists()


def test_import_skill_package_markdown(tmp_path) -> None:
    result = import_skill_package(
        b"",
        target_root=tmp_path,
        filename="custom.md",
        skill_md=SKILL_MD,
    )
    assert result["name"] == "custom-recon"
    assert (tmp_path / "custom-recon" / "SKILL.md").exists()


def test_import_rejects_path_traversal(tmp_path) -> None:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../escape/SKILL.md", SKILL_MD)
    try:
        import_skill_package(
            stream.getvalue(),
            target_root=tmp_path,
            filename="bad.zip",
        )
    except ValueError:
        return
    raise AssertionError("path traversal should be rejected")
