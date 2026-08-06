from __future__ import annotations

from services.control_plane.app.contracts import AgentEvent

from .models import Scenario


class ScenarioOracle:
    def check(
        self,
        scenario: Scenario,
        events: list[AgentEvent],
    ) -> bool:
        findings = {
            str(event.payload.get("finding_id"))
            for event in events
            if event.event_type in ("finding.verified", "loop.succeeded")
            and event.payload.get("finding_id")
        }
        return all(expected in findings for expected in scenario.expected_findings)
