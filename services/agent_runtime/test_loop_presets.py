from __future__ import annotations

from services.agent_runtime.kernel.loop_presets import (
    REGISTRY,
    resolve_loop_profiles,
)


def test_registry_exposes_reusable_presets() -> None:
    ids = {preset.preset_id for preset in REGISTRY.list()}
    assert {
        "nikto-focused",
        "web-scan",
        "code-audit",
        "authz-matrix",
        "ssrf-callback",
        "graphql",
        "websocket",
        "host-recon",
        "ad-attack",
        "cloud-postexploit",
    }.issubset(ids)


def test_resolve_preset_merges_user_overrides_per_role() -> None:
    resolved = resolve_loop_profiles(
        preset_id="nikto-focused",
        user_overrides={
            "scanner": {
                "knowledge_query": ("custom_query",),
                "budget": {"tool_calls": 99},
            }
        },
    )
    scanner = resolved["scanner"]
    assert scanner["knowledge_query"] == ("custom_query",)
    assert scanner["budget"]["tool_calls"] == 99
    assert "web-nikto" in scanner["allowed_skills"]
    assert "recon" in resolved


def test_unknown_preset_returns_user_overrides_only() -> None:
    user = {"scanner": {"allowed_skills": ("web-nikto",)}}
    assert resolve_loop_profiles(preset_id="missing", user_overrides=user) == user


def test_no_preset_keeps_user_overrides() -> None:
    user = {"scanner": {"budget": {"tool_calls": 3}}}
    assert resolve_loop_profiles(preset_id=None, user_overrides=user) == user
