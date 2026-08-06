from __future__ import annotations

import re

from .models import ToolDefinition, ToolPackManifest


DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
RISK_LEVELS = ("L1", "L2", "L3", "L4")


class ConformanceHarness:
    def check_manifest(self, manifest: ToolPackManifest) -> list[str]:
        failures = []
        if not manifest.name:
            failures.append("name is required")
        if not manifest.version:
            failures.append("version is required")
        if not manifest.license:
            failures.append("license is required")
        if manifest.image and not manifest.digest:
            failures.append("image requires pinned digest")
        if manifest.digest and not DIGEST_PATTERN.match(manifest.digest):
            failures.append("digest must be sha256:<64 hex>")
        if not manifest.capabilities:
            failures.append("capabilities is required")
        if not any(
            runner in ("container", "browser", "native", "remote")
            for runner in manifest.runner_requirements
        ):
            failures.append(
                "runner_requirements must include container/browser/native/remote"
            )
        if manifest.image and "container" not in manifest.runner_requirements:
            failures.append("image requires container runner")
        if manifest.network not in ("none", "egress_proxy", "direct"):
            failures.append("network must be none|egress_proxy|direct")
        if not manifest.tools:
            failures.append("tools is required")
        return failures

    def check_tool_definition(self, tool: ToolDefinition) -> list[str]:
        failures = []
        if not tool.ref:
            failures.append("ref is required")
        if not tool.schema:
            failures.append("schema is required")
        if tool.risk_level not in RISK_LEVELS:
            failures.append(f"risk_level must be one of {RISK_LEVELS}")
        if tool.timeout_seconds <= 0:
            failures.append("timeout_seconds must be positive")
        if tool.max_output_bytes <= 0:
            failures.append("max_output_bytes must be positive")
        if tool.runner not in (
            "native",
            "container",
            "remote",
            "browser",
            "mcp",
        ):
            failures.append("runner must be native|container|remote|browser|mcp")
        return failures
