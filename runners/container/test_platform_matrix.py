from __future__ import annotations

from runners.container.platform_matrix import (
    build_platform_matrix,
    expected_assurance,
)


def test_expected_assurance_matches_enforcement_matrix() -> None:
    linux = expected_assurance("linux")
    windows = expected_assurance("windows")
    macos = expected_assurance("macos")

    assert linux["expected_s2_assurance"] is True
    assert windows["expected_s2_assurance"] is False
    assert macos["expected_s2_assurance"] is False
    assert "non_root" in windows["missing"]


def test_build_platform_matrix_covers_three_platforms() -> None:
    matrix = build_platform_matrix(current_platform="windows")

    assert [entry["platform"] for entry in matrix["entries"]] == [
        "linux",
        "windows",
        "macos",
    ]
    assert matrix["current_platform"] == "windows"
    assert matrix["real_docker"] is None
