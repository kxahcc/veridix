from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LicensePolicy:
    allowed: tuple[str, ...]

    def check(self, license_name: str) -> str:
        if not license_name:
            return "unknown"
        for allowed in self.allowed:
            if license_name.lower() == allowed.lower():
                return "allowed"
        return "blocked"


@dataclass(frozen=True)
class SbomPolicyReport:
    components: tuple[tuple[str, str, str], ...]

    @property
    def blocked(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(item for item in self.components if item[2] == "blocked")

    @property
    def unknown(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(item for item in self.components if item[2] == "unknown")


def check_sbom_policy(sbom: dict, policy: LicensePolicy) -> SbomPolicyReport:
    components = []
    for component in sbom.get("components", []):
        name = component.get("name", "")
        licenses = component.get("licenses", [])
        license_name = ""
        if licenses:
            license_name = str(licenses[0].get("license", {}).get("name", "")) or str(
                licenses[0]
            )
        components.append((name, license_name, policy.check(license_name)))
    return SbomPolicyReport(components=tuple(components))


def enforce_sbom_policy(sbom: dict, policy: LicensePolicy) -> None:
    report = check_sbom_policy(sbom, policy)
    if report.blocked or report.unknown:
        blocked = [item[0] for item in report.blocked + report.unknown]
        raise ValueError(f"sbom policy blocked components: {', '.join(blocked)}")
