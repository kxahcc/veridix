from __future__ import annotations

import json

from services.tool_pack.environment import (
    capture_environment,
    write_environment_snapshot,
)


def test_capture_environment_is_stable_and_digested() -> None:
    first = capture_environment()
    second = capture_environment()

    assert len(first["packs"]) == 9
    assert len(first["digest"]) == 64
    assert first["digest"] == second["digest"]
    assert {pack["name"] for pack in first["packs"]} >= {
        "base",
        "web",
        "vulnscan",
    }


def test_write_environment_snapshot_creates_json(tmp_path) -> None:
    target = write_environment_snapshot(
        tmp_path / "runtime" / "tool-environment.json"
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["digest"]
    assert target.exists()
