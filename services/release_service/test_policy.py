from __future__ import annotations

import pytest

from services.release_service.policy import (
    LicensePolicy,
    check_sbom_policy,
    enforce_sbom_policy,
)


def test_sbom_policy_allows_and_blocks_components() -> None:
    policy = LicensePolicy(allowed=("Apache-2.0", "MIT"))
    sbom = {
        "components": [
            {
                "type": "library",
                "name": "pypi:fastapi",
                "version": "0.141.1",
                "licenses": [{"license": {"name": "MIT"}}],
            },
            {
                "type": "library",
                "name": "pypi:unknown-dep",
                "version": "1.0",
                "licenses": [{"license": {"name": "Proprietary"}}],
            },
            {
                "type": "library",
                "name": "pypi:no-license",
                "version": "1.0",
                "licenses": [],
            },
        ]
    }

    report = check_sbom_policy(sbom, policy)

    assert report.blocked[0][0] == "pypi:unknown-dep"
    assert report.unknown[0][0] == "pypi:no-license"
    with pytest.raises(ValueError, match="sbom policy blocked"):
        enforce_sbom_policy(sbom, policy)


def test_sbom_policy_passes_when_all_allowed() -> None:
    policy = LicensePolicy(allowed=("Apache-2.0",))
    sbom = {
        "components": [
            {
                "type": "library",
                "name": "a",
                "licenses": [{"license": {"name": "Apache-2.0"}}],
            }
        ]
    }

    enforce_sbom_policy(sbom, policy)
    report = check_sbom_policy(sbom, policy)
    assert report.blocked == ()
    assert report.unknown == ()
