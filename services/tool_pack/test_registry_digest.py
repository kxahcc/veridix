from __future__ import annotations

import json

from services.tool_pack.registry import ToolRegistry


def _manifest(digest: str) -> dict:
    return {
        "name": "test-pack",
        "version": "0.1.0",
        "image": "veridix-tools",
        "digest": digest,
        "license": "MIT",
        "capabilities": ["test"],
        "runner_requirements": ["container"],
        "network": "none",
        "files": {"read": [], "write": []},
        "tools": ["test.tool"],
        "tool_definitions": [
            {
                "ref": "test.tool",
                "name": "test.tool",
                "description": "test",
                "schema": {"type": "object", "properties": {}},
                "risk_level": "L1",
                "runner": "container",
                "timeout_seconds": 30,
                "max_output_bytes": 1000,
                "command_template": ["true"],
            }
        ],
    }


def test_verify_local_image_digests_reports_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    digest = "sha256:" + "a" * 64
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(_manifest(digest)),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.load_manifest(path)
    monkeypatch.setattr(registry, "_digest_present", lambda record: False)

    assert registry.verify_local_image_digests() == [
        f"test-pack:veridix-tools@{digest}"
    ]

    record = registry.install("test-pack")
    assert record.health == "digest_mismatch"


def test_verify_local_image_digests_passes_when_present(
    tmp_path,
    monkeypatch,
) -> None:
    digest = "sha256:" + "b" * 64
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(_manifest(digest)),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.load_manifest(path)
    monkeypatch.setattr(registry, "_digest_present", lambda record: True)

    assert registry.verify_local_image_digests() == []
    assert registry.install("test-pack").health == "ok"
