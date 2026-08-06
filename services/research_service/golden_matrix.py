from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from services.agent_runtime.golden import GoldenRunDriver, GoldenRunSpec
from services.control_plane.app.contracts import AgentEvent
from services.evidence_service.models import FindingStatus

from .baseline import (
    BaselineComparison,
    compare_to_baseline,
    load_baseline,
)
from .benchmark import BenchmarkRunner
from .models import Scenario


DEFAULT_MISSION = (
    "Use the shell.probe tool against the target once, "
    "then call run.finish with the result."
)


@dataclass(frozen=True)
class GoldenMatrixProvider:
    provider_id: str
    model: str
    endpoint: str
    api_key_ref: str | None = None
    mission: str = DEFAULT_MISSION
    max_turns: int = 5


@dataclass(frozen=True)
class GoldenMatrixRow:
    provider_id: str
    model: str
    runs: int
    aggregate: dict[str, Any]
    comparisons: tuple[BaselineComparison, ...]
    meets_baseline: bool
    harness_digest: str
    behavior_snapshot_id: str


@dataclass(frozen=True)
class GoldenMatrixReport:
    scenario_id: str
    baseline_path: str
    rows: tuple[GoldenMatrixRow, ...]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )


def run_golden_matrix(
    providers: list[GoldenMatrixProvider],
    scenario: Scenario,
    *,
    baseline_path: str,
    runs: int = 1,
) -> GoldenMatrixReport:
    baseline = load_baseline(baseline_path)
    rows: list[GoldenMatrixRow] = []
    for provider in providers:
        state: dict[str, str] = {
            "harness_digest": "",
            "behavior_snapshot_id": "",
        }

        def runner(_scenario: Scenario) -> list[AgentEvent]:
            events, harness_digest, behavior_snapshot_id = _run_provider_once(
                provider,
                _scenario,
            )
            state["harness_digest"] = harness_digest
            state["behavior_snapshot_id"] = behavior_snapshot_id
            return events

        benchmark = BenchmarkRunner(runner).run(scenario, runs=runs)
        comparisons = tuple(compare_to_baseline(benchmark.aggregate, baseline))
        rows.append(
            GoldenMatrixRow(
                provider_id=provider.provider_id,
                model=provider.model,
                runs=runs,
                aggregate=benchmark.aggregate,
                comparisons=comparisons,
                meets_baseline=all(item.meets for item in comparisons),
                harness_digest=state["harness_digest"],
                behavior_snapshot_id=state["behavior_snapshot_id"],
            )
        )
    return GoldenMatrixReport(
        scenario_id=scenario.scenario_id,
        baseline_path=baseline_path,
        rows=tuple(rows),
    )


def _run_provider_once(
    provider: GoldenMatrixProvider,
    scenario: Scenario,
) -> tuple[list[AgentEvent], str, str]:
    token = uuid4().hex[:8]
    result = GoldenRunDriver(timeout_seconds=30).run(
        GoldenRunSpec(
            run_id=f"matrix_{provider.provider_id}_{token}",
            mission=provider.mission,
            target_ref=scenario.target_ref,
            behavior_snapshot=f"behavior_matrix_{provider.provider_id}_{token}",
            provider_endpoint=provider.endpoint,
            provider_model=provider.model,
            api_key_ref=provider.api_key_ref,
            max_turns=provider.max_turns,
        )
    )
    events = list(result.events)
    if result.finding is not None and result.finding.status == FindingStatus.VERIFIED:
        sequence = max((event.sequence or 0 for event in events), default=0) + 1
        events.append(
            AgentEvent(
                event_id=f"{result.run_id}:finding.verified:{token}",
                event_type="finding.verified",
                stream_id=result.run_id,
                run_id=result.run_id,
                actor="research",
                sequence=sequence,
                payload={"finding_id": result.finding.finding_id},
            )
        )
    return events, result.harness_digest, result.behavior_snapshot_id
