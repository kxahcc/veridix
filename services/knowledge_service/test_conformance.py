from __future__ import annotations

import sys
from pathlib import Path

from services.knowledge_service.conformance import McpConformanceHarness
from services.knowledge_service.mcp_connector import LocalMcpConnector

ROOT = Path(__file__).resolve().parents[2]


def test_mcp_conformance_harness_passes() -> None:
    connector = LocalMcpConnector(
        [sys.executable, "-m", "services.knowledge_service.mock_mcp_server"],
        cwd=str(ROOT),
    )
    harness = McpConformanceHarness(max_bytes=8)

    report = harness.run(
        connector,
        node_type="web_discovery",
        allowed_tools=("web_lookup",),
    )

    assert report.passed is True
    names = {check.name for check in report.checks}
    assert names == {
        "lazy_discovery",
        "minimal_projection",
        "trust_untrusted",
        "schema_preview",
        "result_guard",
    }


def test_mcp_conformance_fails_on_unallowed_tools() -> None:
    connector = LocalMcpConnector(
        [sys.executable, "-m", "services.knowledge_service.mock_mcp_server"],
        cwd=str(ROOT),
    )
    harness = McpConformanceHarness()

    report = harness.run(
        connector,
        node_type="web_discovery",
        allowed_tools=("not.allowed",),
    )

    assert report.passed is False
    projection = next(check for check in report.checks if check.name == "minimal_projection")
    assert projection.passed is False
