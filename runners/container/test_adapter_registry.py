from __future__ import annotations

import pytest

from runners.container.adapter_registry import RunnerAdapterRegistry
from runners.container.runner_port import FakeSandboxBackend
from runners.container.sandbox_spec import SandboxValidationError


def test_registry_fails_closed_for_s3_s4_by_default() -> None:
    registry = RunnerAdapterRegistry()

    assert isinstance(registry.create("S1"), FakeSandboxBackend)
    with pytest.raises(SandboxValidationError, match="S3/S4 require"):
        registry.create("S3")
    with pytest.raises(SandboxValidationError, match="S3/S4 require"):
        registry.create("S4")


def test_registry_accepts_explicit_capability_pack_adapter() -> None:
    registry = RunnerAdapterRegistry()

    class S4Adapter(FakeSandboxBackend):
        pass

    registry.register("S4", S4Adapter)

    assert isinstance(registry.create("S4"), S4Adapter)
    assert "S4" in registry.profiles()
