from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any
from uuid import uuid4

from services.control_plane.app.contracts import AgentEvent
from services.evidence_service.evidence_store import EvidenceStore
from services.evidence_service.models import Evidence, Finding, FindingStatus
from services.evidence_service.service import EvidenceService
from services.research_service.trajectory import compute_metrics

from .kernel.contracts import AgentRunSpec
from .kernel.fake_runner import FakeRunner
from .kernel.kernel import AgentKernel
from .kernel.memory import InMemoryCheckpointStore, InMemoryEventSink
from .kernel.tool_broker import ToolBroker
from .provider.openai_adapter import OpenAICompatibleTurnBackend
from .provider.openai_adapter import DEFAULT_TOOL_SCHEMAS


@dataclass(frozen=True)
class GoldenRunSpec:
    run_id: str
    mission: str
    target_ref: str
    behavior_snapshot: str
    provider_endpoint: str
    provider_model: str
    api_key_ref: str | None = None
    allowed_tools: tuple[str, ...] = ("shell.probe", "run.finish")
    max_turns: int = 5
    expected_findings: tuple[str, ...] = ()
    thinking_mode: str | None = None
    tool_choice: str | None = None
    streaming: bool = False


@dataclass(frozen=True)
class GoldenResult:
    run_id: str
    status: str
    events: tuple[AgentEvent, ...]
    metrics: dict[str, Any]
    finding: Finding | None = None
    evidence_refs: tuple[str, ...] = ()
    oracle_passed: bool = False
    harness_digest: str = ""
    behavior_snapshot_id: str = ""
    error: str | None = None


class GoldenRunDriver:
    """Runs the Reference fixture through a real provider and records evidence."""

    def __init__(
        self,
        *,
        evidence_store: EvidenceStore | None = None,
        timeout_seconds: float = 10.0,
        runner_factory=None,
    ) -> None:
        self._evidence_store = evidence_store or EvidenceStore(":memory:")
        self._evidence_service = EvidenceService(self._evidence_store)
        self._timeout_seconds = timeout_seconds
        self._runner_factory = runner_factory

    def run(self, spec: GoldenRunSpec) -> GoldenResult:
        tool_schemas = {
            name: DEFAULT_TOOL_SCHEMAS[name]
            for name in spec.allowed_tools
            if name in DEFAULT_TOOL_SCHEMAS
        }
        backend = OpenAICompatibleTurnBackend(
            base_url=spec.provider_endpoint,
            model=spec.provider_model,
            api_key=spec.api_key_ref,
            timeout_seconds=self._timeout_seconds,
            tool_schemas=tool_schemas,
            thinking_mode=spec.thinking_mode,
            tool_choice=spec.tool_choice,
            streaming=spec.streaming,
        )
        kernel_spec = AgentRunSpec(
            run_id=spec.run_id,
            mission_id="mission_golden",
            target_ref=spec.target_ref,
            behavior_snapshot=spec.behavior_snapshot,
            allowed_targets=(spec.target_ref,),
            allowed_tools=spec.allowed_tools,
            max_turns=spec.max_turns,
            mission=spec.mission,
        )
        runner = self._runner_factory() if self._runner_factory else FakeRunner()
        broker = ToolBroker(runner)
        events = InMemoryEventSink()
        checkpoints = InMemoryCheckpointStore()
        kernel = AgentKernel(kernel_spec, backend, broker, events, checkpoints)

        kernel.start()
        try:
            status = kernel.submit(spec.mission)
        except Exception as error:
            event_list = events.replay(spec.run_id)
            return GoldenResult(
                run_id=spec.run_id,
                status="failed",
                events=tuple(event_list),
                metrics=compute_metrics(event_list),
                oracle_passed=False,
                evidence_refs=(),
                error=str(error),
            )
        event_list = events.replay(spec.run_id)
        metrics = compute_metrics(event_list)

        finding: Finding | None = None
        evidence_refs: tuple[str, ...] = ()
        evidence = Evidence(
            evidence_id=f"ev_golden_{uuid4().hex[:8]}",
            source_type="reference-single-agent",
            target_ref=spec.target_ref,
            action_ref="shell.probe",
            tool_version=spec.behavior_snapshot,
            artifact_refs=[f"artifact://{spec.run_id}/stdout"],
            replay_proof={
                "run_id": spec.run_id,
                "status": status.value,
            },
            confidence=0.8,
        )
        finding = self._evidence_service.submit_candidate(
            target_ref=spec.target_ref,
            vuln_category="golden",
            endpoint=spec.target_ref,
            evidence=evidence,
        )
        if status.value == "succeeded":
            self._evidence_service.support(finding.finding_id)
            finding = self._evidence_service.verify(finding.finding_id, oracle="verified")
            evidence_refs = tuple(finding.evidence_ids)

        oracle_passed = (
            status.value == "succeeded"
            and finding.status == FindingStatus.VERIFIED
        )
        harness_digest = hashlib.sha256(
            json.dumps(
                {
                    "target": spec.target_ref,
                    "behavior": spec.behavior_snapshot,
                    "tools": list(spec.allowed_tools),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return GoldenResult(
            run_id=spec.run_id,
            status=status.value,
            events=tuple(event_list),
            metrics=metrics,
            finding=finding,
            evidence_refs=evidence_refs,
            oracle_passed=oracle_passed,
            harness_digest=harness_digest,
            behavior_snapshot_id=f"behavior_{spec.run_id}",
        )
