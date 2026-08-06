from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolPackManifest:
    name: str
    version: str
    image: str = ""
    digest: str = ""
    license: str = "MIT"
    capabilities: tuple[str, ...] = ()
    runner_requirements: tuple[str, ...] = ("container",)
    network: str = "egress_proxy"
    files: dict[str, list[str]] = field(
        default_factory=lambda: {"read": [], "write": []}
    )
    risk_defaults: dict[str, str] = field(default_factory=dict)
    tools: tuple[str, ...] = ()
    healthcheck: tuple[str, ...] = ()
    signed: bool = False
    source: str = "local"

    def image_ref(self) -> str:
        return f"{self.image}@{self.digest}" if self.digest else self.image


@dataclass(frozen=True)
class ToolDefinition:
    ref: str
    name: str
    description: str = ""
    schema: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "L1"
    capability: str = "tool_calling"
    runner: str = "container"
    sandbox_profile: str = "S2"
    timeout_seconds: int = 30
    max_output_bytes: int = 1_000_000
    output_parser: str = "text"
    command_template: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    failure_codes: tuple[str, ...] = (
        "timeout",
        "denied",
        "tool_unavailable",
    )
    pack: str = ""


@dataclass
class ToolPackRecord:
    manifest: ToolPackManifest
    status: str = "discovered"
    health: str = "unknown"
    enabled_profiles: tuple[str, ...] = ()
    events: list[dict[str, Any]] = field(default_factory=list)
