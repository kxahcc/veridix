from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from services.control_plane.app.contracts import AgentEvent, utc_now


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    ATTENTION_REQUIRED = "attention_required"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WORKER_LOST = "worker_lost"


@dataclass(frozen=True)
class AgentRunSpec:
    run_id: str
    mission_id: str
    target_ref: str
    behavior_snapshot: str
    allowed_targets: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    max_turns: int = 5
    mission: str = "discover and verify one lab finding"
    provider_model: str = ""
    provider_endpoint: str = ""
    wall_clock_seconds: float | None = None
    max_tool_risk: str = "L4"
    budget_policy: str = "pause_and_resume"
    max_context_tokens: int = 128_000


@dataclass(frozen=True)
class ContextView:
    mission: str
    target_ref: str
    observations: tuple[dict[str, Any], ...]
    remaining_budget: int
    context_blocks: "ContextBlocks" | None = None


@dataclass(frozen=True)
class ContextBlocks:
    """Sanitized, citation-bearing context blocks injected into model input."""

    knowledge: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    mcp: tuple[str, ...] = ()
    summaries: tuple[str, ...] = ()
    digest: str = ""

    @property
    def empty(self) -> bool:
        return not any((self.knowledge, self.memory, self.skills, self.mcp))

    def as_dict(self) -> dict[str, object]:
        return {
            "knowledge": list(self.knowledge),
            "memory": list(self.memory),
            "skills": list(self.skills),
            "mcp": list(self.mcp),
            "summaries": list(self.summaries),
            "digest": self.digest,
        }


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelEvent:
    type: str
    text: str | None = None
    tool_call: ToolCall | None = None
    reasoning_content: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExecutionRequest:
    action_id: str
    run_id: str
    tool_ref: str
    input: dict[str, Any]
    idempotency_key: str
    timeout_seconds: int = 30


@dataclass(frozen=True)
class ExecutionResult:
    action_id: str
    status: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    artifact_refs: tuple[str, ...] = ()
    side_effect_state: str = "known"
    observations: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ExecutionOutcome:
    result: ExecutionResult
    replayed: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    risk_level: str
    rule: str
    explanation: str = ""


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    cursor: int
    state: dict[str, Any]
    transcript: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ActionProposal:
    action_id: str
    tool_ref: str
    input: dict[str, Any]
    reasoning: str = ""
    risk_level: str = "L1"


@dataclass(frozen=True)
class ModelDecision:
    kind: str
    action: ActionProposal | None = None
    actions: tuple[ActionProposal, ...] = ()
    reasoning: str = ""


@dataclass(frozen=True)
class OracleResult:
    status: str
    evidence_refs: tuple[str, ...] = ()
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FactRecord:
    fact_id: str
    subject: str
    predicate: str
    value: str
    source_refs: tuple[str, ...] = ()
    confidence: float = 0.5
    trust: str = "project_observed"
    observed_at: str = field(default_factory=utc_now)
    expires_at: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CoverageRecord:
    observed: tuple[str, ...] = ()
    known: tuple[str, ...] = ()

    @property
    def ratio(self) -> float:
        if not self.known:
            return 1.0
        return len(set(self.observed) & set(self.known)) / len(self.known)


@dataclass(frozen=True)
class LoopSpec:
    loop_id: str
    profile: str
    version: str = "1.0"
    max_iterations: int = 10
    allowed_tools: tuple[str, ...] = ()
    stop_on_coverage: float = 1.0
    budget: dict[str, Any] = field(default_factory=dict)
    inputs: tuple[str, ...] = ()
    state_schema: str = ""
    context_policy: str = ""
    allowed_skills: tuple[str, ...] = ()
    knowledge_query: tuple[str, ...] = ()
    oracle: str = ""
    success_criteria: str = ""
    failure_policy: str = ""
    retry_policy: str = ""
    risk_level: str = ""
    evidence_requirements: tuple[str, ...] = ()
    sandbox_profile: str = ""


@dataclass(frozen=True)
class LoopState:
    loop_id: str
    spec_ref: str
    iteration: int = 0
    status: str = "created"
    pending_observations: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    coverage: CoverageRecord = CoverageRecord()
    retry_counts: dict[str, int] = field(default_factory=dict)
    last_error: str | None = None
    checkpoint_ref: str | None = None
    last_tool_observations: tuple[dict[str, Any], ...] = ()
    observation_history: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class LoopResult:
    status: str
    facts: tuple[FactRecord, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    candidate_findings: tuple[str, ...] = ()
    coverage: CoverageRecord = CoverageRecord()
    stop_reason: str | None = None
    oracle_result: OracleResult | None = None
    metrics: "LoopMetrics | None" = None


@dataclass(frozen=True)
class LoopToolResult:
    status: str
    observations: tuple[dict[str, Any], ...] = ()
    facts: tuple[FactRecord, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    error: str = ""
    retryable: bool = False
    error_category: str = ""


@dataclass(frozen=True)
class LoopMetrics:
    iterations: int
    tool_calls: int
    tool_errors: int
    denied: int
    retries: int
    replan_count: int
    duplicate_actions: int
    evidence_count: int
    token_estimate: int
    completion: bool
    success: bool
    verified_result: bool
    stop_accuracy: float
    tool_selection_accuracy: float
    duplicate_action_rate: float
    progress_ratio: float


@dataclass(frozen=True)
class LoopEvent:
    loop_id: str
    event_type: str
    sequence: int
    iteration: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScriptItem:
    text: str
    tool_call: ToolCall | None = None
    finish: bool = False


@dataclass(frozen=True)
class NodeSpec:
    node_id: str
    node_type: str
    loop_spec: LoopSpec | None = None
    preconditions: tuple[str, ...] = ()
    edge_conditions: tuple[str, ...] = ()
    human_prompt: str = ""
    allowed_tools: tuple[str, ...] = ()
    harness_profile: str = "default"
    knowledge_view: str = "mission"
    sandbox_profile: str = "S2"
    oracle_ref: str | None = None
    required_capability: str = "tool_calling"


@dataclass(frozen=True)
class ProjectionSnapshot:
    node_id: str
    included_tools: tuple[str, ...]
    included_skills: tuple[str, ...]
    knowledge_refs: tuple[str, ...]
    omitted: tuple[dict[str, str], ...]
    provider_capability: str
    trust_notes: tuple[str, ...] = ()
    memory_digest: str = ""


@dataclass(frozen=True)
class HarnessSnapshot:
    harness_id: str
    node_id: str
    graph_version: str
    target_ref: str
    scope_hash: str
    auth_context_ref: str
    tool_projection_digest: str
    skill_projection_digest: str
    knowledge_view_digest: str
    memory_view_digest: str
    sandbox_profile: str
    network_profile: str
    oracle_policy: str
    stop_policy: str
    budget_policy: str
    provider_capability: str
    builder_version: str
    built_at: str = field(default_factory=utc_now)
