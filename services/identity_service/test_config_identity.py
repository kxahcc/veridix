from __future__ import annotations

import json

from services.identity_service.config_identity import (
    load_runtime_versions,
    load_tool_environment,
    product_identity_digest,
)


def test_product_identity_digest_is_deterministic_and_sensitive() -> None:
    config = {"security": {"targetScope": {"allowed": ["https://a.test"]}}}
    tool_environment = {"digest": "env_1", "packs": ["web"]}
    runtime_versions = {"runtime": {"node": "24.14.0"}}

    first = product_identity_digest(
        config=config,
        tool_environment=tool_environment,
        runtime_versions=runtime_versions,
    )
    second = product_identity_digest(
        config=config,
        tool_environment=tool_environment,
        runtime_versions=runtime_versions,
    )

    assert first == second
    assert first != product_identity_digest(
        config={**config, "runtime": {"dir": "runtime-clean"}},
        tool_environment=tool_environment,
        runtime_versions=runtime_versions,
    )
    assert first != product_identity_digest(
        config=config,
        tool_environment={"digest": "env_2", "packs": ["web"]},
        runtime_versions=runtime_versions,
    )
    assert first != product_identity_digest(
        config=config,
        tool_environment=tool_environment,
        runtime_versions={"runtime": {"node": "22.0.0"}},
    )


def test_load_tool_environment_and_runtime_versions(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "tool-environment.json").write_text(
        json.dumps({"digest": "env_abc", "builder_version": "tool-env-1"}),
        encoding="utf-8",
    )
    versions = tmp_path / "versions.json"
    versions.write_text(
        json.dumps({"runtime": {"node": "24.14.0"}}),
        encoding="utf-8",
    )

    assert load_tool_environment(runtime)["digest"] == "env_abc"
    assert load_runtime_versions(versions)["runtime"]["node"] == "24.14.0"
    assert load_tool_environment(tmp_path / "missing")["available"] is False
