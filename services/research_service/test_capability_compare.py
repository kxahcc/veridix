from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "capability_compare.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "capability_compare",
        SCRIPT,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_capability_compare_report_structure() -> None:
    module = _load_module()
    report = module.build_report()

    assert report["product"] == "veridix"
    assert set(report["baselines"]) == {
        "strix",
        "cyberstrike",
        "cyberstrikeai",
    }
    assert "capability_map" in report
    assert "agent_architecture" in report["capability_map"]
    assert report["external_validation"]["real_provider_gates"]["overall"] == (
        "passed"
    )
    assert report["external_validation"]["tool_image_smoke"]["rows"] >= 13
    assert report["external_validation"]["rag_hybrid"]["chunks"] >= 313
