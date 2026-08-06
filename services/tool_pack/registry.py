from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .conformance import ConformanceHarness
from .models import ToolDefinition, ToolPackManifest, ToolPackRecord


DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")


class ToolRegistry:
    """Registry with the documented extension lifecycle and audit events."""

    def __init__(
        self,
        *,
        harness: ConformanceHarness | None = None,
    ) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._packs: dict[str, ToolPackRecord] = {}
        self._harness = harness or ConformanceHarness()

    def load_manifest(self, path: str | Path) -> ToolPackRecord:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        manifest = ToolPackManifest(
            name=payload["name"],
            version=payload.get("version", "0.1.0"),
            image=payload.get("image", ""),
            digest=payload.get("digest", ""),
            license=payload.get("license", "MIT"),
            capabilities=tuple(payload.get("capabilities", [])),
            runner_requirements=tuple(
                payload.get("runner_requirements", ["container"])
            ),
            network=payload.get("network", "egress_proxy"),
            files=payload.get("files", {"read": [], "write": []}),
            risk_defaults=payload.get("risk_defaults", {}),
            tools=tuple(payload.get("tools", [])),
            healthcheck=tuple(payload.get("healthcheck", [])),
            signed=bool(payload.get("signed", False)),
            source=payload.get("source", "local"),
        )
        failures = self._harness.check_manifest(manifest)
        if failures:
            raise ValueError(
                f"tool pack {manifest.name} failed conformance: {failures}"
            )
        record = ToolPackRecord(manifest=manifest, status="validated")
        if not payload.get("enabled", True):
            record.status = "disabled"
        self._packs[manifest.name] = record
        self._record(manifest.name, "discovered", "manifest validated")
        if record.status == "disabled":
            return record
        for raw in payload.get("tool_definitions", []):
            raw["pack"] = manifest.name
            self._register_tool(ToolDefinition(**raw))
        return record

    def install(self, name: str) -> ToolPackRecord:
        record = self._require(name)
        if record.status == "disabled":
            return record
        if record.manifest.image and not self._digest_present(record):
            record.status = "installed"
            record.health = "digest_mismatch"
            self._record(
                name,
                "installed",
                "pinned digest does not match a local image; rebuild or update manifests",
            )
            return record
        record.status = "installed"
        record.health = "ok" if self._probe(record) else "unknown"
        self._record(
            name,
            "installed",
            f"health={record.health}",
        )
        return record

    def verify_local_image_digests(self) -> list[str]:
        """Return pack/image refs whose pinned digest is not present locally."""
        mismatches: list[str] = []
        for record in self._packs.values():
            if record.status == "disabled":
                continue
            manifest = record.manifest
            if not manifest.digest:
                continue
            if not self._digest_present(record):
                mismatches.append(f"{manifest.name}:{manifest.image_ref()}")
        return mismatches

    def enable(self, name: str, profile: str) -> ToolPackRecord:
        record = self._require(name)
        if profile == "experimental" and not record.manifest.signed:
            pass
        elif record.health != "ok":
            raise ValueError(
                f"tool pack {name} must be installed and healthy before enable"
            )
        if profile not in record.enabled_profiles:
            record.enabled_profiles = (
                *record.enabled_profiles,
                profile,
            )
            self._record(name, "enabled", f"profile={profile}")
        return record

    def disable(self, name: str, profile: str) -> ToolPackRecord:
        record = self._require(name)
        record.enabled_profiles = tuple(
            item for item in record.enabled_profiles if item != profile
        )
        self._record(name, "disabled", f"profile={profile}")
        return record

    def upgrade(self, name: str, manifest: ToolPackManifest) -> ToolPackRecord:
        record = self._require(name)
        failures = self._harness.check_manifest(manifest)
        if failures:
            raise ValueError(f"upgrade failed conformance: {failures}")
        record.manifest = manifest
        record.status = "validated"
        self._record(
            name,
            "upgraded",
            f"version={manifest.version}",
        )
        return record

    def delete(self, name: str) -> None:
        record = self._require(name)
        self._record(name, "deleted", "")
        self._packs.pop(name, None)

    def get(self, ref: str) -> ToolDefinition | None:
        return self._tools.get(ref)

    def list(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))

    def entries_for(
        self,
        names: tuple[str, ...],
    ) -> dict[str, ToolDefinition]:
        return {
            name: self._tools[name]
            for name in names
            if name in self._tools
        }

    def pack_for(self, ref: str) -> ToolPackManifest:
        definition = self._tools[ref]
        return self._require(definition.pack).manifest

    def pack_events(self, name: str) -> tuple[dict[str, Any], ...]:
        return tuple(self._require(name).events)

    def _register_tool(self, definition: ToolDefinition) -> None:
        failures = self._harness.check_tool_definition(definition)
        if failures:
            raise ValueError(
                f"tool {definition.ref} failed conformance: {failures}"
            )
        self._tools[definition.ref] = definition

    def _require(self, name: str) -> ToolPackRecord:
        if name not in self._packs:
            raise KeyError(f"tool pack {name} not loaded")
        return self._packs[name]

    def _record(self, name: str, event: str, detail: str) -> None:
        self._require(name).events.append(
            {
                "event": event,
                "pack": name,
                "detail": detail,
                "at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }
        )

    def _digest_present(self, record: ToolPackRecord) -> bool:
        if not record.manifest.digest:
            return True
        if not DIGEST_PATTERN.match(record.manifest.digest):
            return False
        try:
            import docker
            from docker.errors import ImageNotFound

            client = docker.from_env()
            try:
                client.images.get(record.manifest.image_ref())
                return True
            except ImageNotFound:
                pass
            images = client.images.list()
            return any(
                any(
                    digest.endswith(record.manifest.digest)
                    for digest in image.attrs.get("RepoDigests") or []
                )
                for image in images
            )
        except Exception:
            return False

    def _probe(self, record: ToolPackRecord) -> bool:
        if not record.manifest.healthcheck:
            return True
        try:
            import docker

            client = docker.from_env()
            result = client.containers.run(
                record.manifest.image_ref(),
                record.manifest.healthcheck,
                detach=False,
                remove=True,
            )
            return True
        except Exception:
            return False
