from __future__ import annotations

import json
from pathlib import Path

from .models import Scenario


def load_scenario(path: str | Path) -> Scenario:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Scenario(
        scenario_id=data["scenario_id"],
        name=data["name"],
        target_ref=data["target_ref"],
        expected_findings=tuple(data.get("expected_findings", ())),
        max_turns=int(data.get("max_turns", 5)),
        mode=data.get("mode", "single"),
    )


def load_all(directory: str | Path) -> list[Scenario]:
    scenarios = []
    for path in sorted(Path(directory).glob("*.json")):
        scenarios.append(load_scenario(path))
    return scenarios
