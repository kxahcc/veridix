from __future__ import annotations

from services.control_plane.app.registry import RuntimeRegistry


def test_skill_registry_roundtrip() -> None:
    registry = RuntimeRegistry(":memory:")
    registry.upsert_skill(
        "web.discovery",
        "web.discovery",
        "0.1.0",
        "available",
        trigger="web_discovery",
        runner="browser",
        risk_level="L1",
    )

    rows = registry.list("skills")

    assert rows[0]["skill_ref"] == "web.discovery"
    assert rows[0]["version"] == "0.1.0"
    assert rows[0]["runner"] == "browser"
    registry.close()


def test_mcp_registry_roundtrip() -> None:
    registry = RuntimeRegistry(":memory:")
    registry.upsert_mcp(
        "mcp_caido",
        "caido",
        "available",
        kind="container",
        command="python -m mcp_caido",
    )

    rows = registry.list("mcp_servers")

    assert rows[0]["server_id"] == "mcp_caido"
    assert rows[0]["kind"] == "container"
    assert rows[0]["command"] == "python -m mcp_caido"
    registry.close()


def test_skill_upsert_is_idempotent() -> None:
    registry = RuntimeRegistry(":memory:")
    registry.upsert_skill("s1", "s1", "0.1.0", "available")
    registry.upsert_skill(
        "s1",
        "s1",
        "0.2.0",
        "available",
        trigger="web_test",
    )

    rows = registry.list("skills")

    assert len(rows) == 1
    assert rows[0]["version"] == "0.2.0"
    registry.close()
