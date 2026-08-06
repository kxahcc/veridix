from __future__ import annotations

import pytest

from services.knowledge_service.models import SkillManifest
from services.knowledge_service.skills import (
    SkillRegistry,
    sign_manifest,
    verify_manifest,
)


KEY = "test-signing-key"


def _manifest() -> SkillManifest:
    return SkillManifest(
        name="web.discovery",
        version="0.1.0",
        trigger="web_discovery",
        required_tools=("browser.open",),
        required_runner="browser",
    )


def test_sign_and_verify_roundtrip() -> None:
    signed = sign_manifest(_manifest(), KEY)

    assert signed.signature
    assert verify_manifest(signed, KEY) is True
    assert verify_manifest(signed, "wrong-key") is False


def test_tampered_manifest_fails_verification() -> None:
    signed = sign_manifest(_manifest(), KEY)
    tampered = SkillManifest(
        **{
            **signed.__dict__,
            "risk_level": "L4",
        }
    )

    assert verify_manifest(tampered, KEY) is False


def test_registry_rejects_unsigned_with_key() -> None:
    registry = SkillRegistry()

    with pytest.raises(ValueError):
        registry.register(
            {
                "name": "web.discovery",
                "version": "0.1.0",
                "trigger": "web_discovery",
            },
            verify_key=KEY,
        )


def test_registry_accepts_signed_manifest_with_key() -> None:
    registry = SkillRegistry()
    signed = sign_manifest(_manifest(), KEY)

    registered = registry.register(
        {
            **signed.__dict__,
            "required_tools": list(signed.required_tools),
        },
        verify_key=KEY,
    )

    assert registered.name == "web.discovery"
