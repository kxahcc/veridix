from __future__ import annotations

from typing import Callable

from .runner_port import FakeSandboxBackend, RunnerPort
from .sandbox_spec import SandboxValidationError


class RunnerAdapterRegistry:
    """Fail-closed Runner adapter selection by sandbox assurance profile."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], RunnerPort]] = {
            "S0": FakeSandboxBackend,
            "S1": FakeSandboxBackend,
        }

    def register(self, profile: str, factory: Callable[[], RunnerPort]) -> None:
        self._factories[profile] = factory

    def create(self, profile: str) -> RunnerPort:
        factory = self._factories.get(profile)
        if factory is None:
            raise SandboxValidationError(
                f"no runner adapter for profile {profile}; "
                "S3/S4 require an explicit capability-pack adapter"
            )
        return factory()

    def profiles(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))
