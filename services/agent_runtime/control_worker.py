from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import httpx

from services.control_plane.app.contracts import AgentEvent, utc_now
from services.tool_pack.execution import validate_tool_arguments

from .kernel.contracts import (
    AgentRunSpec,
    ContextView,
    LoopSpec,
    NodeSpec,
    RunStatus,
)
from .kernel.contracts import ContextBlocks
from .kernel.context import DataLabel, ProviderProfile
from .kernel.context_trimming import BackendSummarizer
from .kernel.harness import (
    HarnessBuilder,
    KnowledgeEntry,
    ProviderCapability,
    SkillEntry,
    ToolEntry,
)
from .kernel.tool_pack import ToolRegistry
from .kernel.fake_runner import FakeRunner
from .kernel.fault_injector import FaultInjector
from .kernel.kernel import AgentKernel
from .kernel.memory import (
    InMemoryEventSink,
    SqliteCheckpointStore,
)
from .kernel.memory_tools import MEMORY_TOOL_REFS, MemoryToolRunner
from .kernel.tool_broker import ToolBroker
from runners.web.normalizer import classify_auth_state
from .provider.openai_adapter import DEFAULT_TOOL_SCHEMAS, OpenAICompatibleTurnBackend
from .evidence import (
    derive_connector_findings,
    derive_finding_from_observations,
)
from .oracle import FindingOracle
from .kernel.loop import LoopRunner
from .kernel.loop_adapters import BrokerLoopTool, TurnLoopModelAdapter
from .kernel.loop_profiles import apply_loop_profile
from .kernel.composite_tool_runner import CompositeToolRunner
from .kernel.mcp_tool_runner import McpToolRunner
from .roles import (
    AgentRole,
    RoleGraphRunner,
    authz_matrix_role_template,
    build_role_oracle,
    code_audit_role_template,
    graphql_role_template,
    redteam_orchestration_role_template,
    scanner_verify_role_template,
    ssrf_callback_role_template,
    websocket_role_template,
    webappsec_role_template,
)
from services.mission_orchestrator.graph_store import GraphStore
from services.mission_orchestrator.planner import (
    CandidateVerifierPlanner,
    ChainPlanner,
    FailureDrivenReplanner,
)
from .role_benchmark import compare_single_vs_multi_role
from services.research_service.behaviors import snapshot_from_components
from services.identity_service.config_identity import (
    load_runtime_versions,
    load_tool_environment,
    product_identity_digest,
)
from services.knowledge_service.embedding_adapters import (
    create_embedding,
    create_rerank,
)
from services.knowledge_service.graph_store import KnowledgeGraphStore
from services.knowledge_service.graph_backends import create_knowledge_graph
from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.loader import (
    load_knowledge_dir,
    load_skills_dir,
)
from services.knowledge_service.retrieval import RetrievalEngine
from services.knowledge_service.skills import SkillRegistry
from services.knowledge_service.skill_retrieval import SkillRetriever
from services.knowledge_service.sparse_encoder import sparse_encode
from services.knowledge_service.sqlite_memory import (
    ProjectMemoryStore,
    SqliteProjectMemory,
)
from services.knowledge_service.vector_backends import create_vector_store
from .context_projector import (
    ContextProjector,
    ContextRequest,
    default_mcp_factory,
    node_type_for_target,
    runner_for_node_type,
)
from .context_assembly import ContextAssembler, ContextAssemblyResult
from .storage_provisioning import ensure_storage_config


def scanner_verify_policy(spec: dict[str, Any]) -> dict[str, Any]:
    """Extract the verifier finding policy from a mission spec."""
    return {
        "min_severity": str(spec.get("min_severity") or ""),
        "require_evidence": bool(spec.get("require_evidence", True)),
        "required_metadata_fields": tuple(
            spec.get("required_metadata_fields", ())
        ),
        "dedupe": bool(spec.get("dedupe", True)),
        "conflict_blocks": bool(spec.get("conflict_blocks", True)),
    }


def _derive_allowed_tools(
    spec: dict[str, Any],
    default: tuple[str, ...],
) -> tuple[str, ...]:
    allowed = tuple(spec.get("allowed_tools") or ())
    if allowed:
        return _with_system_tools(allowed)
    derived = tuple(
        spec.get("scanner_tools") or spec.get("code_tools") or ()
    )
    if derived:
        return _with_system_tools((*derived, "run.finish"))
    return _with_system_tools(default)


def _fallback_node_from_config(config: dict[str, Any]) -> NodeSpec | None:
    node_id = str(config.get("node_id") or "")
    if not node_id:
        return None
    node_type = str(config.get("node_type") or "loop")
    loop_cfg = config.get("loop_spec") or {}
    loop_spec = (
        apply_loop_profile(
            LoopSpec(
                loop_id=str(loop_cfg.get("loop_id") or node_id),
                profile=str(loop_cfg.get("profile") or "web_discovery"),
                max_iterations=int(
                    loop_cfg.get("max_iterations") or 4
                ),
                allowed_tools=tuple(loop_cfg.get("allowed_tools") or ()),
                stop_on_coverage=float(
                    loop_cfg.get("stop_on_coverage") or 1.0
                ),
                budget=dict(loop_cfg.get("budget") or {}),
                inputs=tuple(loop_cfg.get("inputs") or ()),
                state_schema=str(loop_cfg.get("state_schema") or ""),
                context_policy=str(loop_cfg.get("context_policy") or ""),
                allowed_skills=tuple(loop_cfg.get("allowed_skills") or ()),
                knowledge_query=tuple(loop_cfg.get("knowledge_query") or ()),
                oracle=str(loop_cfg.get("oracle") or ""),
                success_criteria=str(loop_cfg.get("success_criteria") or ""),
                failure_policy=str(loop_cfg.get("failure_policy") or ""),
                retry_policy=str(loop_cfg.get("retry_policy") or ""),
                risk_level=str(loop_cfg.get("risk_level") or ""),
                evidence_requirements=tuple(
                    loop_cfg.get("evidence_requirements") or ()
                ),
                sandbox_profile=str(loop_cfg.get("sandbox_profile") or ""),
            )
        )
        if node_type == "loop"
        else None
    )
    return NodeSpec(
        node_id=node_id,
        node_type=node_type,
        loop_spec=loop_spec,
        allowed_tools=tuple(config.get("allowed_tools") or ()),
        harness_profile=str(config.get("harness_profile") or "default"),
        oracle_ref=config.get("oracle_ref"),
        sandbox_profile=str(config.get("sandbox_profile") or "S2"),
        preconditions=tuple(config.get("preconditions") or ()),
    )


def _with_system_tools(tools: tuple[str, ...]) -> tuple[str, ...]:
    if "skill.read" in tools:
        return tools
    return (*tools, "skill.read")


def _with_memory_tools(tools: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tools, *MEMORY_TOOL_REFS)))


def _create_turn_backend(
    *,
    endpoint: str,
    model: str,
    api_key_ref: str | None,
    tool_schemas: dict[str, dict],
    tool_descriptions: dict[str, str],
    provider_config: dict[str, Any],
    options: WorkerOptions,
):
    backend_kind = str(provider_config.get("backend") or "openai").lower()
    common = dict(
        model=model,
        api_key=api_key_ref,
        tool_schemas=tool_schemas,
        tool_descriptions=tool_descriptions,
        max_tokens=(
            int(provider_config.get("max_tokens") or options.max_tokens)
        ),
        timeout_seconds=(
            provider_config.get("timeout_seconds")
            or options.timeout_seconds
        ),
        thinking_mode=(
            provider_config.get("thinking_mode")
            or options.thinking_mode
        ),
        tool_choice=options.tool_choice,
        streaming=(
            provider_config.get("streaming")
            if provider_config.get("streaming") is not None
            else options.streaming
        ),
        reasoning_effort=provider_config.get("reasoning_effort"),
        retries=int(provider_config.get("retries") or 5),
        json_mode=bool(provider_config.get("json_mode")),
    )
    if backend_kind == "litellm":
        from .provider.litellm_adapter import LiteLLMTurnBackend

        return LiteLLMTurnBackend(
            **common,
            api_base=endpoint or None,
            litellm_provider=str(
                provider_config.get("litellm_provider") or ""
            ),
        )
    return OpenAICompatibleTurnBackend(
        base_url=endpoint,
        **common,
    )


TERMINAL_RUN_EVENTS = frozenset(
    {
        "run.queued",
        "run.claimed",
        "run.paused",
        "run.resumed",
        "run.cancelled",
        "run.succeeded",
        "run.failed",
    }
)


class WorkerControlError(RuntimeError):
    pass


class SpoolOverflow(RuntimeError):
    pass


class ControlPlaneClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retries = 3

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: dict) -> Any:
        return self._request("POST", path, body)

    def list_runs(self) -> list[dict]:
        return self.get("/api/v1/runs")

    def get_run(self, run_id: str) -> dict:
        return self.get(f"/api/v1/runs/{run_id}")

    def get_mission(self, mission_id: str) -> dict:
        return self.get(f"/api/v1/missions/{mission_id}")

    def get_target(self, target_id: str) -> dict:
        return self.get(f"/api/v1/targets/{target_id}")

    def get_events(self, run_id: str, after: int = 0) -> list[dict]:
        return self.get(f"/api/v1/runs/{run_id}/events?after={after}")

    def get_provider_default(self) -> dict | None:
        try:
            return self._best_effort_get("/api/v1/settings/provider-default")
        except WorkerControlError:
            return None

    def get_retrieval_default(self) -> dict | None:
        try:
            return self._best_effort_get("/api/v1/settings/retrieval")
        except WorkerControlError:
            return None

    def register_runner(
        self,
        runner_id: str,
        kind: str,
        status: str = "online",
    ) -> dict:
        return self.post(
            "/api/v1/runtime/runners",
            {
                "runner_id": runner_id,
                "kind": kind,
                "status": status,
            },
        )

    def _best_effort_get(self, path: str) -> Any:
        """Read optional control-plane config without stalling run setup."""
        try:
            return httpx.get(
                f"{self._base_url}{path}",
                timeout=min(self._timeout, 3.0),
                trust_env=False,
            ).json()
        except httpx.HTTPError as error:
            raise WorkerControlError(str(error)) from error

    def get_role_template(self, template_id: str) -> dict | None:
        try:
            return self.get(f"/api/v1/runtime/role-templates/{template_id}")
        except WorkerControlError:
            return None

    def post_remote_dispatch(
        self,
        node_id: str,
        task_ref: str,
        payload: dict,
    ) -> dict:
        return self.post(
            f"/api/v1/remote/nodes/{node_id}/dispatch",
            {
                "task_ref": task_ref,
                "payload": payload,
                "lease_seconds": 600,
            },
        )

    def get_remote_results(self, node_id: str) -> list[dict]:
        return self.get(f"/api/v1/remote/nodes/{node_id}/results")

    def claim(self, run_id: str, worker_id: str, idempotency_key: str) -> dict:
        return self.post(
            f"/api/v1/runs/{run_id}/claim",
            {"worker_id": worker_id, "idempotency_key": idempotency_key},
        )

    def pause_run(self, run_id: str, idempotency_key: str) -> dict:
        return self.post(
            f"/api/v1/runs/{run_id}/pause",
            {"idempotency_key": idempotency_key},
        )

    def finish(
        self,
        run_id: str,
        outcome: str,
        idempotency_key: str,
        *,
        stop_reason: str = "",
        summary: str = "",
    ) -> dict:
        return self.post(
            f"/api/v1/runs/{run_id}/finish",
            {
                "outcome": outcome,
                "idempotency_key": idempotency_key,
                "stop_reason": stop_reason,
                "summary": summary,
            },
        )

    def post_event(
        self,
        run_id: str,
        event_id: str,
        event_type: str,
        payload: dict,
    ) -> dict:
        return self.post(
            f"/api/v1/runs/{run_id}/events",
            {
                "event_id": event_id,
                "event_type": event_type,
                "actor": "agent-worker",
                "payload": payload,
            },
        )

    def post_web_observations(self, run_id: str, observations: list[dict]) -> dict:
        return self.post(
            f"/api/v1/runs/{run_id}/web-observations",
            {"observations": observations},
        )

    def submit_finding(
        self,
        run_id: str,
        *,
        target_ref: str,
        vuln_category: str,
        endpoint: str,
        notes: str = "",
        severity: str | None = None,
        evidence: dict | None = None,
    ) -> dict:
        body = {
            "target_ref": target_ref,
            "vuln_category": vuln_category,
            "endpoint": endpoint,
            "notes": notes,
        }
        if severity:
            body["severity"] = severity
        if evidence is not None:
            body["evidence"] = evidence
        return self.post(
            f"/api/v1/runs/{run_id}/findings",
            body,
        )

    def support_finding(self, finding_id: str) -> dict:
        return self.post(f"/api/v1/findings/{finding_id}/support", {})

    def verify_finding(self, finding_id: str, oracle: str = "verified") -> dict:
        return self.post(
            f"/api/v1/findings/{finding_id}/verify",
            {"oracle": oracle},
        )

    def get_finding(self, finding_id: str) -> dict:
        return self.get(f"/api/v1/findings/{finding_id}")

    def append_finding_note(self, finding_id: str, note: str) -> dict:
        return self.post(
            f"/api/v1/findings/{finding_id}/notes",
            {"note": note},
        )

    def register_provider(
        self,
        provider_id: str,
        model: str,
        endpoint: str,
    ) -> dict:
        return self.post(
            "/api/v1/runtime/providers",
            {
                "provider_id": provider_id,
                "model": model,
                "endpoint": endpoint,
                "status": "ok",
            },
        )

    def register_skill(
        self,
        skill_ref: str,
        name: str,
        version: str,
        *,
        trigger: str = "",
        runner: str = "",
        risk_level: str = "L1",
    ) -> dict:
        return self.post(
            "/api/v1/runtime/skills",
            {
                "skill_ref": skill_ref,
                "name": name,
                "version": version,
                "status": "available",
                "trigger": trigger,
                "runner": runner,
                "risk_level": risk_level,
            },
        )

    def register_mcp(
        self,
        server_id: str,
        name: str,
        *,
        kind: str = "local",
        command: str = "",
    ) -> dict:
        return self.post(
            "/api/v1/runtime/mcp",
            {
                "server_id": server_id,
                "name": name,
                "status": "available",
                "kind": kind,
                "command": command,
            },
        )

    def get_human_gates(self, run_id: str) -> dict:
        return self.get(f"/api/v1/runs/{run_id}/human-gates")

    def resolve_human_gate(
        self,
        run_id: str,
        node_id: str,
        approved: bool,
        reason: str = "",
    ) -> dict:
        return self.post(
            f"/api/v1/runs/{run_id}/human-gates/{node_id}/resolve",
            {
                "approved": approved,
                "reason": reason,
            },
        )

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        idempotent = method == "GET" or bool(
            body
            and (
                "idempotency_key" in body
                or "event_id" in body
            )
        )
        max_attempts = self._retries
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = httpx.request(
                    method,
                    f"{self._base_url}{path}",
                    json=body,
                    timeout=self._timeout,
                    trust_env=False,
                )
            except httpx.HTTPError as error:
                last_error = error
                transient = isinstance(
                    error,
                    (httpx.TimeoutException, httpx.ConnectError),
                )
                if attempt + 1 < max_attempts and (
                    transient or idempotent
                ):
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise WorkerControlError(str(error)) from error
            break
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("detail", ""))
            except Exception:
                pass
            raise WorkerControlError(
                f"{method} {path} -> {response.status_code}: {detail}"
            )
        return response.json()


class ControlPlaneEventSink(InMemoryEventSink):
    """Local replay buffer that also streams worker events to the control plane."""

    def __init__(
        self,
        client: ControlPlaneClient,
        run_id: str,
        *,
        spool_limit: int = 1000,
    ) -> None:
        super().__init__()
        self._client = client
        self._run_id = run_id
        self._spool: list[AgentEvent] = []
        self._spool_limit = spool_limit
        self._overflow = False

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def overflow(self) -> bool:
        return self._overflow

    def emit(
        self,
        *,
        stream_id: str,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ):
        events = self._events.setdefault(stream_id, [])
        sequence = len(events) + 1
        event = AgentEvent(
            event_id=f"{run_id}:{event_type}:{uuid4().hex[:8]}",
            event_type=event_type,
            stream_id=stream_id,
            run_id=run_id,
            actor=actor,
            occurred_at=utc_now(),
            sequence=sequence,
            payload=payload,
        )
        events.append(event)
        if event_type not in TERMINAL_RUN_EVENTS:
            try:
                self._client.post_event(
                    run_id,
                    event.event_id,
                    event.event_type,
                    event.payload,
                )
            except WorkerControlError:
                if len(self._spool) >= self._spool_limit:
                    self._overflow = True
                    raise SpoolOverflow(
                        f"event spool full for run {run_id}"
                    ) from None
                self._spool.append(event)
        return event

    def flush(self) -> bool:
        if not self._spool:
            return True
        try:
            for event in list(self._spool):
                self._client.post_event(
                    self._run_id,
                    event.event_id,
                    event.event_type,
                    event.payload,
                )
            self._spool.clear()
            return True
        except WorkerControlError:
            return False


@dataclass(frozen=True)
class WorkerOptions:
    worker_id: str = "agent-worker"
    poll_interval_seconds: float = 1.0
    max_turns: int = 50
    timeout_seconds: float = 15.0
    max_tokens: int = 1024
    provider_endpoint: str | None = None
    provider_model: str | None = None
    api_key_ref: str | None = None
    allowed_tools: tuple[str, ...] = ("shell.probe", "run.finish")
    thinking_mode: str | None = None
    tool_choice: str | None = None
    streaming: bool = False
    golden_finding: bool = False
    checkpoint_dir: str | None = None
    spool_limit: int = 1000
    memory_db: str | None = None
    context_assets_dir: str | None = None
    wall_clock_seconds: float | None = None
    max_tool_risk: str = "L4"
    runtime_dir: str | None = None


class ControlPlaneRunWorker:
    """Claims control-plane runs and executes them through the agent kernel."""

    def __init__(
        self,
        client: ControlPlaneClient,
        *,
        runner_factory: Callable[[], Any] | None = None,
        options: WorkerOptions | None = None,
    ) -> None:
        self._client = client
        self._runner_factory = runner_factory or (lambda: FakeRunner())
        self._options = options or WorkerOptions()
        try:
            self._client.register_runner(
                self._options.worker_id,
                os.environ.get("VERIDIX_RUNNER", "docker"),
                "online",
            )
        except Exception:
            # Registration is diagnostic; run polling still works when the
            # control plane is temporarily unavailable.
            pass
        self._checkpoint_dir = Path(
            self._options.checkpoint_dir
            or os.environ.get("VERIDIX_CHECKPOINT_DIR")
            or (
                Path(os.environ.get("VERIDIX_RUNTIME_DIR", "runtime"))
                / "checkpoints"
            )
        )
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._loop_checkpoint_store = SqliteCheckpointStore(
            self._checkpoint_dir / "loop-checkpoints.sqlite3"
        )
        self._runtime_dir = Path(
            self._options.runtime_dir
            or os.environ.get("VERIDIX_RUNTIME_DIR", "runtime")
        )
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            default_retrieval = self._client.get_retrieval_default() or {}
        except Exception:
            default_retrieval = {}
        try:
            retrieval_config = ensure_storage_config(
                default_retrieval,
                runtime_dir=self._runtime_dir,
            )
            self._write_storage_snapshot(retrieval_config)
        except Exception:
            # The snapshot is diagnostic only; run setup still retries it.
            pass
        self._memory_db = Path(
            self._options.memory_db
            or os.environ.get("VERIDIX_MEMORY_DB")
            or (self._runtime_dir / "memory.db")
        )
        self._assets_dir = Path(
            self._options.context_assets_dir
            or (
                Path(__file__).resolve().parents[2]
            )
        )
        self._knowledge_store: KnowledgeStore | None = None
        self._skill_registry: SkillRegistry | None = None
        self._skill_registry_signature: tuple[float, ...] | None = None
        self._memory_store: ProjectMemoryStore | None = None
        self._parsed_finding_seen: dict[str, set[tuple[str, str]]] = {}
        self._graph_store: KnowledgeGraphStore | None = None
        self._vector_store: Any | None = None
        self._knowledge_loaded = False
        self._vectors_indexed = False
        self._skills_synced = False
        self._skill_registry_generation = 0
        self._projector_skill_generation = -1
        self._active: dict[str, _ActiveRun] = {}
        self._projectors: dict[str, ContextProjector] = {}
        self._projections: dict[str, Any] = {}
        self._assembled: dict[str, ContextAssemblyResult] = {}
        self._projection_events_emitted: set[str] = set()

    def poll_once(self, *, timeout_seconds: float = 300.0) -> list[str]:
        claimed_run_ids: list[str] = []
        for run in self._client.list_runs():
            run_id = run["run_id"]
            status = run.get("status")
            if status == "queued":
                try:
                    self._start_run(run_id)
                    claimed_run_ids.append(run_id)
                except Exception as error:
                    self._mark_failed(run_id, error)
            elif (
                status == "paused"
                and run_id not in self._active
            ):
                try:
                    self._resume_paused_run(run_id)
                except Exception:
                    # No checkpoint yet means the pause happened before the
                    # worker started; keep the run paused for a later resume.
                    pass
            elif (
                status == "running"
                and run_id not in self._active
            ):
                try:
                    self._adopt_running_run(run_id)
                except Exception:
                    pass
        deadline = time.time() + timeout_seconds
        while self._active and time.time() < deadline:
            self._reconcile_active()
            if not any(
                self._is_running(active) for active in self._active.values()
            ):
                break
            time.sleep(0.2)
        if self._active:
            # Worker threads have finished; post any pending terminal state
            # before returning so callers observe the control-plane outcome.
            self._reconcile_active()
        return claimed_run_ids

    def _start_run(self, run_id: str) -> None:
        run = self._client.get_run(run_id)
        self._client.claim(
            run_id,
            self._options.worker_id,
            f"{run_id}:claim",
        )
        agent_spec, runner, kernel, _, events, spec, mission = self._build_execution(
            run_id,
            run,
        )
        if spec.get("mode") == "graph":
            self._run_graph_mode(run_id, agent_spec, spec, events)
            return
        if spec.get("mode") == "multi_role":
            self._run_multi_role_mode(
                run_id,
                agent_spec,
                spec,
                events,
                provider=spec.get("provider") or {},
                mission=mission,
            )
            return
        active = _ActiveRun(
            run_id=run_id,
            agent_spec=agent_spec,
            kernel=kernel,
            runner=runner,
            events=events,
            finding_hint=_finding_hint(spec),
            project_id=str(mission.get("project_id") or "default"),
        )
        kernel.start()
        self._post_harness_snapshot(
            events,
            run_id,
            agent_spec,
            mission,
        )
        active.thread = threading.Thread(
            target=self._run_kernel,
            args=(active, False),
            daemon=True,
        )
        active.thread.start()
        self._active[run_id] = active

    def _build_execution(
        self,
        run_id: str,
        run: dict,
    ) -> tuple[
        AgentRunSpec,
        Any,
        AgentKernel,
        SqliteCheckpointStore,
        ControlPlaneEventSink,
        dict,
        dict,
    ]:
        mission = self._client.get_mission(run["mission_id"])
        spec = mission.get("spec") or {}
        target = self._client.get_target(str(spec.get("target_id", "")))
        target_ref = target["url"]
        provider = spec.get("provider") or {}
        if not provider:
            default_provider = self._client.get_provider_default()
            if default_provider:
                provider = default_provider
        endpoint = (
            provider.get("endpoint")
            or self._options.provider_endpoint
            or os.environ.get("VERIDIX_PROVIDER_ENDPOINT")
        )
        model = (
            provider.get("model")
            or self._options.provider_model
            or os.environ.get("VERIDIX_PROVIDER_MODEL")
        )
        if not endpoint or not model:
            raise WorkerControlError(
                "provider endpoint/model not configured for worker"
            )
        allowed_tools = _derive_allowed_tools(
            spec,
            tuple(self._options.allowed_tools),
        )
        allowed_tools = _with_memory_tools(allowed_tools)
        tool_schemas = _tool_schema_map(
            _load_tool_registry(),
            allowed_tools,
        )
        tool_descriptions = _tool_description_map(
            _load_tool_registry(),
            allowed_tools,
        )
        provider_config = provider.get("config") or {}
        backend = _create_turn_backend(
            endpoint=endpoint,
            model=model,
            api_key_ref=(
                provider.get("api_key_ref")
                or self._options.api_key_ref
            ),
            tool_schemas=tool_schemas,
            tool_descriptions=tool_descriptions,
            provider_config=provider_config,
            options=self._options,
        )
        try:
            self._client.register_provider(
                "agent-worker",
                model,
                endpoint,
            )
        except Exception:
            pass
        behavior_snapshot = str(
            spec.get("behavior_snapshot") or f"behavior_{run_id}"
        )
        allowed_targets = (target_ref, *tuple(target.get("allowed") or ()))
        agent_spec = AgentRunSpec(
            run_id=run_id,
            mission_id=mission["mission_id"],
            target_ref=target_ref,
            behavior_snapshot=behavior_snapshot,
            allowed_targets=allowed_targets,
            allowed_tools=allowed_tools,
            max_turns=int(
                spec.get("max_turns") or self._options.max_turns
            ),
            mission=str(spec.get("mission") or mission.get("name")),
            provider_model=model,
            provider_endpoint=endpoint,
            wall_clock_seconds=(
                float(spec.get("wall_clock_seconds"))
                if spec.get("wall_clock_seconds") is not None
                else (
                    float(self._options.wall_clock_seconds)
                    if self._options.wall_clock_seconds is not None
                    else None
                )
            ),
            budget_policy=str(
                spec.get("budget_policy") or "continue"
            ),
            max_tool_risk=str(
                spec.get("max_tool_risk")
                or self._options.max_tool_risk
            ),
        )
        self._assemble_context_blocks(mission, agent_spec)
        runner = self._runner_factory()
        runner = self._with_memory_runner(runner, mission, run_id)
        runner = self._with_mcp_runner(runner, mission, run_id)
        risk_registry = _load_tool_registry()
        broker = ToolBroker(
            runner,
            risk_resolver=(
                lambda name: (
                    risk_registry.get(name).risk_level
                    if risk_registry.get(name) is not None
                    else "L1"
                )
            ),
            max_risk_level=agent_spec.max_tool_risk,
        )
        events = ControlPlaneEventSink(
            self._client,
            run_id,
            spool_limit=self._options.spool_limit,
        )
        checkpoints = SqliteCheckpointStore(
            self._checkpoint_dir / "checkpoints.sqlite3"
        )
        kernel = AgentKernel(
            agent_spec,
            backend,
            broker,
            events,
            checkpoints,
            provider_profile=_provider_profile(endpoint),
            context_provider=(
                lambda: self._assembled.get(
                    run_id,
                    ContextAssemblyResult(ContextBlocks()),
                ).blocks
            ),
            summarizer=BackendSummarizer(backend),
        )
        return agent_spec, runner, kernel, checkpoints, events, spec, mission

    def _with_mcp_runner(self, runner: Any, mission: dict, run_id: str) -> Any:
        projection = self._projections.get(run_id)
        mission_spec = mission.get("spec") or {}
        mcp_config = mission_spec.get("mcp")
        if (
            projection is None
            or not projection.mcp_included
            or not mcp_config
        ):
            return runner
        connector = default_mcp_factory(mcp_config)
        mcp_runner = McpToolRunner(connector)
        return CompositeToolRunner(
            {
                preview.name: mcp_runner
                for preview in projection.mcp_included
            },
            default=runner,
        )

    def _with_memory_runner(
        self,
        runner: Any,
        mission: dict,
        run_id: str,
    ) -> Any:
        project_id = str(mission.get("project_id") or "default")

        def memory_provider() -> SqliteProjectMemory:
            return self._memory_for_project(project_id)

        def embedding_provider():
            try:
                projector = self._context_projector_for(mission or {})
                return getattr(projector, "memory_embedding", None)
            except Exception:
                return None

        def invalidate_context() -> None:
            for key in list(self._assembled):
                if key == run_id or key.startswith(f"{run_id}:"):
                    self._assembled.pop(key, None)

        memory_runner = MemoryToolRunner(
            memory_provider=memory_provider,
            embedding_provider=embedding_provider,
            on_memory_changed=invalidate_context,
        )
        return CompositeToolRunner(
            {ref: memory_runner for ref in MEMORY_TOOL_REFS},
            default=runner,
        )

    def _resume_paused_run(self, run_id: str) -> None:
        run = self._client.get_run(run_id)
        if run.get("status") != "paused":
            return
        agent_spec, runner, kernel, checkpoints, events, spec, mission = (
            self._build_execution(run_id, run)
        )
        if spec.get("mode") == "graph":
            self._run_graph_mode(run_id, agent_spec, spec, events)
            return
        if spec.get("mode") == "multi_role" or spec.get("role_template"):
            self._run_multi_role_mode(
                run_id,
                agent_spec,
                spec,
                events,
                provider=spec.get("provider") or {},
                mission=mission,
            )
            return
        if checkpoints.load(run_id) is None:
            return
        self._active[run_id] = _ActiveRun(
            run_id=run_id,
            agent_spec=agent_spec,
            kernel=kernel,
            runner=runner,
            events=events,
            finding_hint=_finding_hint(spec),
            project_id=str(mission.get("project_id") or "default"),
            local_paused=True,
            finished=True,
        )

    def _adopt_running_run(self, run_id: str) -> None:
        run = self._client.get_run(run_id)
        if run.get("status") != "running":
            return
        agent_spec, runner, kernel, checkpoints, events, spec, mission = (
            self._build_execution(run_id, run)
        )
        if spec.get("mode") == "graph":
            self._run_graph_mode(run_id, agent_spec, spec, events)
            return
        if spec.get("mode") == "multi_role" or spec.get("role_template"):
            self._run_multi_role_mode(
                run_id,
                agent_spec,
                spec,
                events,
                provider=spec.get("provider") or {},
                mission=mission,
            )
            return
        if checkpoints.load(run_id) is None:
            return
        active = _ActiveRun(
            run_id=run_id,
            agent_spec=agent_spec,
            kernel=kernel,
            runner=runner,
            events=events,
            finding_hint=_finding_hint(spec),
            project_id=str(mission.get("project_id") or "default"),
        )
        active.thread = threading.Thread(
            target=self._run_kernel,
            args=(active, True),
            daemon=True,
        )
        active.thread.start()
        self._active[run_id] = active

    def _run_kernel(
        self,
        active: _ActiveRun,
        resume: bool,
        user_input: str | None = None,
    ) -> None:
        try:
            if resume:
                status = active.kernel.resume(user_input)
            else:
                status = active.kernel.submit(active.agent_spec.mission)
            if status == RunStatus.PAUSED:
                try:
                    self._client.pause_run(
                        active.run_id,
                        f"{active.run_id}:budget_exhausted:pause",
                    )
                except WorkerControlError:
                    pass
            active.outcome = {"status": status.value, "error": None}
        except SpoolOverflow as error:
            active.outcome = {
                "status": "attention_required",
                "error": str(error),
            }
        except Exception as error:
            active.outcome = {"status": "failed", "error": str(error)}
        finally:
            active.finished = True

    def _reconcile_active(self) -> None:
        for run_id, active in list(self._active.items()):
            try:
                current = self._client.get_run(run_id)
            except Exception:
                continue
            if active.events is not None and not active.events.flush():
                continue
            status = current.get("status")
            if status == "cancelled":
                active.kernel.cancel()
                self._active.pop(run_id, None)
                continue
            if status == "paused":
                if (
                    active.thread is not None
                    and active.thread.is_alive()
                    and not active.local_paused
                ):
                    active.kernel.pause()
                    active.local_paused = True
                continue
            if status != "running":
                continue
            if (
                active.local_paused
                and active.finished
                and status == "running"
            ):
                active.local_paused = False
                active.finished = False
                user_input = self._latest_user_message(run_id)
                active.thread = threading.Thread(
                    target=self._run_kernel,
                    args=(active, True, user_input),
                    daemon=True,
                )
                active.thread.start()
            elif active.finished and not active.local_paused:
                if active.outcome.get("status") == "attention_required":
                    try:
                        self._client.post_event(
                            run_id,
                            f"{run_id}:side_effect_unknown:{uuid4().hex[:8]}",
                            "side_effect_unknown",
                            {
                                "action_id": None,
                                "tool": "agent-worker",
                                "recovery": ["resume", "abort"],
                            },
                        )
                        self._active.pop(run_id, None)
                    except Exception:
                        pass
                    continue
                try:
                    self._finish_active(active, current)
                    self._active.pop(run_id, None)
                except Exception:
                    # Control plane may be briefly unreachable; retry next poll.
                    pass

    def _is_running(self, active: _ActiveRun) -> bool:
        return (
            active.thread is not None and active.thread.is_alive()
        ) or not active.finished

    def _latest_user_message(self, run_id: str) -> str | None:
        try:
            events = self._client.get_events(run_id)
        except Exception:
            return None
        for event in reversed(events):
            if event.get("event_type") == "user.message":
                return str(event.get("payload", {}).get("message", ""))
        return None

    def _finish_active(self, active: _ActiveRun, current: dict) -> None:
        run_id = active.run_id
        error = active.outcome.get("error")
        if error:
            self._client.finish(
                run_id,
                "failed",
                f"{run_id}:finish:error",
                stop_reason="worker_error",
                summary=str(error),
            )
            return
        outcome = (
            "succeeded"
            if active.outcome.get("status") == "succeeded"
            else "failed"
        )
        summary = f"worker finished run {run_id} with {outcome}"
        kernel_observations: list[dict] = []
        if active.kernel is not None:
            try:
                kernel_observations = list(active.kernel.observations())
            except Exception:
                pass
        observations = [
            *kernel_observations,
            *self._collect_observations(active.runner, run_id),
        ]
        self._persist_run_memory(active, observations)
        self._reflect_run(active, observations)
        self._submit_connector_findings(active, observations)
        self._submit_parsed_findings(active)
        if outcome == "succeeded":
            if self._options.golden_finding:
                finding = self._client.submit_finding(
                    run_id,
                    target_ref=active.agent_spec.target_ref,
                    vuln_category="golden",
                    endpoint=active.agent_spec.target_ref,
                    notes=summary,
                )
                self._client.support_finding(finding["finding_id"])
                self._client.verify_finding(
                    finding["finding_id"],
                    oracle="verified",
                )
                self._record_finding_memory(active, finding)
            elif active.finding_hint and active.finding_hint.get("marker"):
                finding_hint = derive_finding_from_observations(
                    observations,
                    target_ref=active.agent_spec.target_ref,
                    vuln_category=active.finding_hint["category"],
                    marker=active.finding_hint["marker"],
                )
                verdict = FindingOracle(
                    marker=active.finding_hint["marker"]
                ).evaluate(observations)
                if active.events is not None:
                    active.events.emit(
                        stream_id=run_id,
                        run_id=run_id,
                        event_type="finding.oracle.evaluated",
                        actor="agent-worker",
                        payload={
                            "decision": verdict.decision,
                            "reason": verdict.reason,
                            "replay_proof": verdict.replay_proof,
                        },
                    )
                if (
                    finding_hint is not None
                    and verdict.decision == "verified"
                ):
                    finding = self._client.submit_finding(
                        run_id,
                        **{
                            **finding_hint,
                            "notes": json.dumps(
                                {"replay_proof": verdict.replay_proof},
                                ensure_ascii=True,
                            ),
                        },
                    )
                    self._client.support_finding(finding["finding_id"])
                    self._client.verify_finding(
                        finding["finding_id"],
                        oracle="verified",
                    )
                    self._record_finding_memory(active, finding)
        self._client.finish(
            run_id,
            outcome,
            f"{run_id}:finish",
            stop_reason=active.outcome.get("status", outcome),
            summary=summary,
        )
        if observations:
            self._client.post_web_observations(run_id, observations)

    def _post_harness_snapshot(
        self,
        events: ControlPlaneEventSink,
        run_id: str,
        spec: AgentRunSpec,
        mission: dict,
    ) -> None:
        registry = _load_tool_registry()
        tool_entries = {
            name: ToolEntry(
                name=name,
                risk_level=definition.risk_level,
                required_capability=definition.capability,
            )
            for name, definition in registry.entries_for(
                spec.allowed_tools
            ).items()
        }
        node_type = node_type_for_target(spec.target_ref)
        runner = runner_for_node_type(node_type)
        project_id = str(mission.get("project_id") or "default")
        mission_spec = mission.get("spec") or {}
        if run_id not in self._projections:
            self._assemble_context_blocks(mission, spec)
        context = self._projections[run_id]
        assembly = self._assembled[run_id]
        skill_entries = {
            skill.name: SkillEntry(
                trigger=tuple(skill.trigger),
                version=skill.version,
                min_version="1.0",
                conformance="ok",
            )
            for skill in context.skills.included
        }
        knowledge_entries = {
            chunk.chunk_id: KnowledgeEntry(
                ref=chunk.chunk_id,
                subjects=chunk.subjects,
                trust=chunk.trust,
            )
            for chunk in context.knowledge.chunks
        }
        builder = HarnessBuilder(
            tools=tool_entries,
            skills=skill_entries,
            knowledge=knowledge_entries,
            builder_version="wp05-1",
        )
        node = NodeSpec(
            node_id="reference-single-agent",
            node_type=node_type,
            allowed_tools=spec.allowed_tools,
            sandbox_profile="S2",
            oracle_ref="domain_oracle_required",
            harness_profile=node_type,
        )
        provider_capability = ProviderCapability(
            model_names=(spec.provider_model,),
            health="ok",
            tool_calling=True,
            streaming=True,
            data_policy="local",
        )
        harness, projection = builder.build(
            node,
            provider_capability,
            target_ref=spec.target_ref,
            auth_context_ref="",
            scope_hash=spec.behavior_snapshot,
            memory_digest=context.memory_digest,
        )
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="context.projection",
            actor="agent-worker",
            payload=context.as_event_payload(),
        )
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="context.assembly",
            actor="agent-worker",
            payload={
                "digest": assembly.blocks.digest,
                "knowledge_blocks": len(assembly.blocks.knowledge),
                "memory_blocks": len(assembly.blocks.memory),
                "skill_blocks": len(assembly.blocks.skills),
                "mcp_blocks": len(assembly.blocks.mcp),
                "omitted": [dict(item) for item in assembly.omitted],
                "redacted": assembly.redacted,
            },
        )
        behavior = snapshot_from_components(
            snapshot_id=spec.behavior_snapshot,
            config={
                "provider": spec.provider_model,
                "endpoint": spec.provider_endpoint,
                "tools": list(spec.allowed_tools),
                "max_turns": spec.max_turns,
                "tool_environment_digest": _read_tool_environment_digest(
                    self._runtime_dir
                ),
                "product_identity_digest": _product_identity_for_spec(
                    mission_spec,
                    self._runtime_dir,
                ),
                "config_hash": str(
                    mission_spec.get("config_hash") or ""
                ),
            },
            harness={
                "target": spec.target_ref,
                "behavior": spec.behavior_snapshot,
                "harness_id": harness.harness_id,
                "tool_projection_digest": harness.tool_projection_digest,
                "skill_projection_digest": harness.skill_projection_digest,
                "knowledge_view_digest": harness.knowledge_view_digest,
                "memory_view_digest": harness.memory_view_digest,
            },
            provider=spec.provider_model,
        )
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="behavior.snapshot",
            actor="agent-worker",
            payload={
                "snapshot_id": behavior.snapshot_id,
                "config_hash": behavior.config_hash,
                "harness_digest": behavior.harness_digest,
                "provider": behavior.provider,
                "product_identity_digest": (
                    _product_identity_for_spec(
                        mission_spec,
                        self._runtime_dir,
                    )
                ),
                "behavior_snapshot": spec.behavior_snapshot,
            },
        )
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="harness.snapshot",
            actor="agent-worker",
            payload={
                "harness_id": harness.harness_id,
                "harness_digest": behavior.harness_digest,
                "behavior_snapshot": spec.behavior_snapshot,
                "behavior_snapshot_id": behavior.snapshot_id,
                "config_hash": behavior.config_hash,
                "provider": behavior.provider,
                "target_ref": spec.target_ref,
                "allowed_tools": list(spec.allowed_tools),
                "included_tools": list(projection.included_tools),
                "omitted_tools": list(projection.omitted),
                "tool_projection_digest": harness.tool_projection_digest,
                "skill_projection_digest": harness.skill_projection_digest,
                "knowledge_view_digest": harness.knowledge_view_digest,
                "memory_view_digest": harness.memory_view_digest,
                "builder_version": harness.builder_version,
                "context_digest": context.context_digest,
                "node_type": node_type,
                "knowledge_refs": list(context.knowledge_refs),
                "included_skills": list(context.included_skill_names),
                "omitted": list(context.omitted),
                "memory_active": (
                    context.memory_snapshot.active
                    if context.memory_snapshot is not None
                    else 0
                ),
                "memory_conflict": (
                    context.memory_snapshot.conflict
                    if context.memory_snapshot is not None
                    else 0
                ),
                "memory_stale": (
                    context.memory_snapshot.stale
                    if context.memory_snapshot is not None
                    else 0
                ),
                "rag_level": (
                    context.retrieval.level
                    if context.retrieval is not None
                    else "unavailable"
                ),
                "rag_degraded": list(context.rag_degraded),
                "mcp_included": [
                    tool.name for tool in context.mcp_included
                ],
                "tool_environment_digest": _read_tool_environment_digest(
                    self._runtime_dir
                ),
            },
        )

    def _ensure_knowledge_store(self) -> KnowledgeStore:
        if self._knowledge_store is None:
            self._runtime_dir.mkdir(parents=True, exist_ok=True)
            store = KnowledgeStore(str(self._runtime_dir / "knowledge.db"))
            self._knowledge_store = store
        return self._knowledge_store

    def _ensure_skill_registry(self) -> SkillRegistry:
        signature = self._skill_roots_signature()
        if (
            self._skill_registry is None
            or self._skill_registry_signature != signature
        ):
            registry = SkillRegistry()
            skills_dir = self._assets_dir / "skills" / "builtin"
            if skills_dir.exists():
                load_skills_dir(
                    registry,
                    skills_dir,
                    verify_key=os.environ.get("VERIDIX_SKILL_SIGNING_KEY"),
                )
            runtime_skills = Path(self._runtime_dir) / "skills"
            if runtime_skills.exists():
                load_skills_dir(
                    registry,
                    runtime_skills,
                    verify_key=os.environ.get("VERIDIX_SKILL_SIGNING_KEY"),
                )
            self._skill_registry = registry
            self._skill_registry_signature = signature
            self._skill_registry_generation += 1
        return self._skill_registry

    def _skill_roots_signature(self) -> tuple[float, ...]:
        roots = [
            self._assets_dir / "skills" / "builtin",
            Path(self._runtime_dir) / "skills",
        ]
        stamps: list[float] = []
        for root in roots:
            if not root.exists():
                stamps.append(0.0)
                continue
            latest = 0.0
            for path in root.rglob("*"):
                if path.is_file():
                    latest = max(latest, path.stat().st_mtime)
            stamps.append(latest)
        return tuple(stamps)

    def _ensure_graph_store(
        self,
        config: dict | None = None,
    ) -> KnowledgeGraphStore:
        if self._graph_store is None:
            self._runtime_dir.mkdir(parents=True, exist_ok=True)
            store = create_knowledge_graph(
                config,
                runtime_dir=self._runtime_dir,
            )
            self._graph_store = store
        return self._graph_store

    def _ensure_builtin_assets(
        self,
        graph_config: dict | None = None,
    ) -> None:
        if self._knowledge_loaded:
            return
        store = self._ensure_knowledge_store()
        graph = self._ensure_graph_store(graph_config)
        knowledge_dir = self._assets_dir / "knowledge" / "builtin"
        if knowledge_dir.exists():
            store.clear()
            load_knowledge_dir(
                store,
                knowledge_dir,
                graph_store=None,
            )
            chunks = store.list_chunks()
            existing_graph_chunks = getattr(
                graph,
                "chunk_count",
                lambda: 0,
            )()
            if existing_graph_chunks < len(chunks):
                for chunk in chunks:
                    graph.register_chunk_graph(chunk)
        self._knowledge_loaded = True

    def _index_knowledge_vectors(
        self,
        embedding,
        vector_store,
    ) -> None:
        if self._vectors_indexed or vector_store is None:
            return
        store = self._ensure_knowledge_store()
        embed_query = getattr(embedding, "embed_query", None)
        if embed_query is None:
            return
        try:
            chunks = store.list_chunks()
            existing_count = (
                vector_store.count()
                if hasattr(vector_store, "count")
                else 0
            )
            if existing_count >= len(chunks):
                self._vectors_indexed = True
                return
            precomputed = self._load_builtin_embeddings()
            embed_batch = getattr(embedding, "embed_batch", None)
            entries: list[dict] = []

            def flush(upsert_entries: list[dict]) -> None:
                if not upsert_entries:
                    return
                if hasattr(vector_store, "upsert_batch"):
                    vector_store.upsert_batch(upsert_entries)
                else:
                    for entry in upsert_entries:
                        vector_store.upsert(
                            entry["chunk_id"],
                            entry["vector"],
                            entry["source_ref"],
                            sparse=entry.get("sparse"),
                        )

            if embed_batch is not None:
                for start in range(0, len(chunks), 8):
                    batch = chunks[start:start + 8]
                    pending = [
                        chunk
                        for chunk in batch
                        if chunk.chunk_id not in precomputed
                    ]
                    vectors = (
                        embed_batch([chunk.content for chunk in pending])
                        if pending
                        else []
                    )
                    vector_by_id = {
                        chunk.chunk_id: vector
                        for chunk, vector in zip(pending, vectors)
                    }
                    for chunk in batch:
                        vector = precomputed.get(
                            chunk.chunk_id
                        ) or vector_by_id.get(chunk.chunk_id)
                        if vector is not None:
                            entries.append(
                                {
                                    "chunk_id": chunk.chunk_id,
                                    "vector": vector,
                                    "source_ref": chunk.source_ref,
                                    "project_id": chunk.project_id or "",
                                    "sparse": (
                                        sparse_encode(chunk.content)
                                        if hasattr(
                                            vector_store,
                                            "search_hybrid",
                                        )
                                        else None
                                    ),
                                }
                            )
            else:
                for chunk in chunks:
                    vector = precomputed.get(chunk.chunk_id)
                    if vector is None:
                        vector = embed_query(chunk.content)
                    entries.append(
                        {
                            "chunk_id": chunk.chunk_id,
                            "vector": vector,
                            "source_ref": chunk.source_ref,
                            "project_id": chunk.project_id or "",
                            "sparse": (
                                sparse_encode(chunk.content)
                                if hasattr(vector_store, "search_hybrid")
                                else None
                            ),
                        }
                    )
            for start in range(0, len(entries), 64):
                flush(entries[start : start + 64])
            self._vectors_indexed = True
        except Exception:
            self._vectors_indexed = False

    def _load_builtin_embeddings(self) -> dict[str, list[float]]:
        path = (
            self._assets_dir
            / "knowledge"
            / "builtin"
            / "embeddings.npz"
        )
        meta_path = (
            self._assets_dir
            / "knowledge"
            / "builtin"
            / "embeddings.meta.json"
        )
        if not path.exists():
            return {}
        try:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                model = str(meta.get("model") or "")
                endpoint = str(meta.get("endpoint") or "")
                if model and model != os.environ.get(
                    "VERIDIX_EMBEDDING_MODEL"
                ):
                    return {}
                if endpoint and endpoint != os.environ.get(
                    "VERIDIX_EMBEDDING_ENDPOINT"
                ):
                    return {}
            import numpy as np

            with np.load(path, allow_pickle=False) as data:
                return {
                    str(chunk_id): list(vector)
                    for chunk_id, vector in data.items()
                }
        except Exception:
            return {}

    def _memory_for_project(self, project_id: str) -> SqliteProjectMemory:
        if self._memory_store is None:
            self._memory_store = ProjectMemoryStore(self._memory_db)
        return self._memory_store.get(project_id)

    def _context_projector_for(self, mission: dict) -> ContextProjector:
        if (
            self._projector_skill_generation
            != self._skill_registry_generation
        ):
            self._projectors.clear()
            self._projector_skill_generation = (
                self._skill_registry_generation
            )
        project_id = str(mission.get("project_id") or "default")
        projector = self._projectors.get(project_id)
        if projector is not None:
            return projector
        mission_spec = mission.get("spec") or {}
        retrieval_override = mission_spec.get("retrieval") or {}
        if not retrieval_override:
            try:
                default_retrieval = self._client.get_retrieval_default()
            except AttributeError:
                default_retrieval = {}
            except WorkerControlError:
                default_retrieval = {}
            if default_retrieval:
                retrieval_override = default_retrieval
        retrieval_config = ensure_storage_config(
            retrieval_override,
            runtime_dir=self._runtime_dir,
        )
        graph_config = retrieval_config.get("graph")
        self._ensure_builtin_assets(graph_config)
        knowledge_store = self._ensure_knowledge_store()
        skill_registry = self._ensure_skill_registry()
        memory = self._memory_for_project(project_id)
        level = str(retrieval_config.get("level", "lexical"))
        embedding = create_embedding(
            retrieval_config.get("embedding"),
            runtime_dir=self._runtime_dir,
        )
        if embedding is not None and hasattr(embedding, "warmup"):
            embedding.warmup()
        rerank = create_rerank(
            retrieval_config.get("rerank"),
            runtime_dir=self._runtime_dir,
        )
        vector_store = self._vector_store
        if vector_store is None:
            vector_store = create_vector_store(
                retrieval_config.get("vector_store"),
                runtime_dir=self._runtime_dir,
            )
            self._vector_store = vector_store
        graph_store = self._graph_store
        if embedding is not None and vector_store is not None:
            self._index_knowledge_vectors(embedding, vector_store)
        skill_retriever = None
        if embedding is not None:
            skill_retriever = SkillRetriever(
                registry=skill_registry,
                embedding=embedding,
                rerank=rerank,
                index_path=self._runtime_dir / "skill-vectors.db",
                deadline_seconds=float(
                    retrieval_config.get("deadline_seconds") or 8.0
                ),
                fusion=str(retrieval_config.get("fusion") or "rrf"),
                min_vector_score=float(
                    retrieval_config.get("min_vector_score") or 0
                ),
            )
            if str(os.environ.get("VERIDIX_SKILL_INDEX_SYNC") or "").lower() in (
                "1",
                "true",
                "yes",
            ):
                try:
                    skill_retriever.index()
                except Exception:
                    pass
            else:
                threading.Thread(
                    target=skill_retriever.index,
                    kwargs={"force": False},
                    daemon=True,
                ).start()
        projector = ContextProjector(
            knowledge_store=knowledge_store,
            retrieval_engine=RetrievalEngine(
                knowledge_store,
                embedding=embedding,
                rerank=rerank,
                vector_store=vector_store,
                graph_store=graph_store,
                deadline_seconds=float(
                    retrieval_config.get("deadline_seconds") or 8.0
                ),
                min_vector_score=float(
                    retrieval_config.get("min_vector_score") or 0
                ),
                project_id=project_id,
                fusion=str(retrieval_config.get("fusion") or "rrf"),
            ),
            memory=memory,
            memory_embedding=embedding,
            skill_registry=skill_registry,
            skill_retriever=skill_retriever,
            mcp_factory=default_mcp_factory,
        )
        self._projectors[project_id] = projector
        self._write_storage_snapshot(retrieval_config)
        return projector

    def _write_storage_snapshot(self, retrieval_config: dict) -> None:
        snapshot = {
            "embedding": _embedding_backend_snapshot(retrieval_config),
            "vector_store": _vector_backend_snapshot(retrieval_config),
            "graph": {
                "enabled": True,
                "backend": str(
                    (retrieval_config.get("graph") or {}).get(
                        "backend",
                        "sqlite",
                    )
                ),
            },
            "rerank": {
                "enabled": bool(
                    (retrieval_config.get("rerank") or {}).get("enabled")
                )
            },
        }
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        (self._runtime_dir / "storage.json").write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

    def _assemble_context_blocks(
        self,
        mission: dict,
        spec: AgentRunSpec,
        *,
        loop_spec: LoopSpec | None = None,
    ) -> ContextAssemblyResult:
        projector = self._context_projector_for(mission)
        mission_spec = mission.get("spec") or {}
        memory_config = mission_spec.get("memory") or {}
        retrieval_config = mission_spec.get("retrieval") or {}
        skills_config = mission_spec.get("skills") or {}
        cache_key = (
            f"{spec.run_id}:{loop_spec.loop_id}"
            if loop_spec is not None
            else spec.run_id
        )
        if cache_key in self._assembled:
            return self._assembled[cache_key]
        node_type = node_type_for_target(spec.target_ref)
        knowledge_query = (
            "; ".join(loop_spec.knowledge_query)
            if loop_spec is not None and loop_spec.knowledge_query
            else str(mission_spec.get("knowledge_query") or "")
        )
        allowed_skills = (
            loop_spec.allowed_skills
            if loop_spec is not None
            else ()
        )
        request = ContextRequest(
            project_id=str(mission.get("project_id") or "default"),
            mission=spec.mission,
            target_ref=spec.target_ref,
            node_type=node_type,
            allowed_tools=spec.allowed_tools,
            allowed_skills=allowed_skills,
            runner=runner_for_node_type(node_type),
            knowledge_query=knowledge_query,
            retrieval_level=str(
                retrieval_config.get("level", "lexical")
            ),
            observed_since=(
                str(retrieval_config["observed_since"])
                if retrieval_config.get("observed_since")
                else None
            ),
            observed_until=(
                str(retrieval_config["observed_until"])
                if retrieval_config.get("observed_until")
                else None
            ),
            memory_token_budget=int(
                memory_config.get("token_budget") or 2000
            ),
            memory_limit=int(memory_config.get("limit") or 20),
            memory_retrieval_level=str(
                memory_config.get("retrieval_level", "hybrid")
            ),
            skill_token_budget=int(
                skills_config.get("token_budget") or 12000
            ),
            skill_selection_limit=int(
                skills_config.get("selection_limit") or 6
            ),
            skill_retrieval_level=str(
                skills_config.get("retrieval_level") or "hybrid"
            ),
            mcp_config=mission_spec.get("mcp"),
        )
        projection = projector.project(request)
        self._projections[cache_key] = projection
        mcp_config = mission_spec.get("mcp")
        if mcp_config:
            try:
                self._client.register_mcp(
                    str(mcp_config.get("name") or "mcp"),
                    str(mcp_config.get("name") or "mcp"),
                    kind=str(mcp_config.get("kind") or "local"),
                    command=" ".join(mcp_config.get("command", [])),
                )
            except Exception:
                pass
        assembly = ContextAssembler(
            skill_token_budget=int(
                skills_config.get("token_budget") or 12000
            )
        ).assemble(
            projection,
            _provider_profile(spec.provider_endpoint),
        )
        self._assembled[cache_key] = assembly
        return assembly

    def _emit_node_projection(
        self,
        events: ControlPlaneEventSink,
        run_id: str,
        loop_spec: LoopSpec,
    ) -> None:
        cache_key = f"{run_id}:{loop_spec.loop_id}"
        if cache_key in self._projection_events_emitted:
            return
        projection = self._projections.get(cache_key)
        if projection is None:
            return
        payload = projection.as_event_payload()
        payload["loop_id"] = loop_spec.loop_id
        payload["profile"] = loop_spec.profile
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="context.projection",
            actor="agent-worker",
            payload=payload,
        )
        self._projection_events_emitted.add(cache_key)

    def _persist_run_memory(
        self,
        active: _ActiveRun,
        observations: list[dict],
    ) -> None:
        memory = self._memory_for_project(active.project_id)
        memory.append_summary(
            _compact_run_summary(active.run_id, observations),
            source_ref=active.run_id,
        )
        for observation in observations:
            subject = str(
                observation.get("endpoint")
                or observation.get("url")
                or active.agent_spec.target_ref
            )
            tool = str(observation.get("tool") or "observation")
            stdout = str(observation.get("stdout") or "")[:800]
            if not stdout:
                continue
            fact, inserted = memory.record(
                subject,
                f"observed:{tool}",
                stdout,
                target=active.agent_spec.target_ref,
                source_refs=(
                    *(
                        str(ref)
                        for ref in (
                            observation.get("request_id"),
                            *(
                                observation.get("artifact_refs")
                                or ()
                            ),
                        )
                        if ref
                    ),
                ),
                confidence=0.7,
                expires_at=(
                    datetime.now(timezone.utc)
                    + timedelta(hours=24)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            replay = observation.get("replay_proof")
            if (
                isinstance(replay, dict)
                and replay.get("matched") is False
            ):
                try:
                    memory.mark_stale(
                        fact.fact_id,
                        reason="replay_mismatch",
                    )
                except KeyError:
                    pass
            if inserted and active.events is not None:
                active.events.emit(
                    stream_id=active.run_id,
                    run_id=active.run_id,
                    event_type="memory.fact.appended",
                    actor="agent-worker",
                    payload={
                        "fact_id": fact.fact_id,
                        "subject": fact.subject,
                        "predicate": fact.predicate,
                        "value": fact.value,
                    },
                )

    def _json_backend_for(self, active: _ActiveRun):
        return _create_turn_backend(
            endpoint=active.agent_spec.provider_endpoint,
            model=active.agent_spec.provider_model,
            api_key_ref=(
                active.provider_api_key_ref
                or self._options.api_key_ref
            ),
            tool_schemas={},
            tool_descriptions={},
            provider_config={
                **active.provider_config,
                "json_mode": True,
            },
            options=self._options,
        )

    def _reflect_run(
        self,
        active: _ActiveRun,
        observations: list[dict],
    ) -> None:
        try:
            backend = self._json_backend_for(active)
            summary = _compact_run_summary(active.run_id, observations)
            categories = sorted(
                {
                    str(obs.get("vuln_category") or "")
                    for obs in observations
                    if obs.get("vuln_category")
                }
            )
            prompt = (
                "Reflect on this security testing run and write 2-3 "
                "concise lessons for future runs. Include what worked, "
                "what did not, and reusable next steps. Keep it under "
                "400 words.\n\n"
                f"Summary: {summary}\n"
                f"Finding categories: {','.join(categories) or 'none'}\n"
                f"Target: {active.agent_spec.target_ref}"
            )
            reflection = ""
            for event in backend.stream(
                ContextView(
                    mission=prompt,
                    target_ref=active.agent_spec.target_ref,
                    observations=(),
                    remaining_budget=1,
                    context_blocks=ContextBlocks(),
                )
            ):
                if event.type == "model.finish":
                    if event.payload and event.payload.get("json"):
                        reflection = json.dumps(
                            event.payload["json"],
                            ensure_ascii=True,
                        )
                    elif event.text:
                        reflection += event.text
            if not reflection.strip():
                return
            memory = self._memory_for_project(active.project_id)
            memory.append_summary(
                f"reflection: {reflection.strip()[:1200]}",
                source_ref=f"{active.run_id}:reflection",
            )
            if active.events is not None:
                active.events.emit(
                    stream_id=active.run_id,
                    run_id=active.run_id,
                    event_type="memory.reflection",
                    actor="agent-worker",
                    payload={
                        "summary": summary,
                        "reflection": reflection.strip()[:1200],
                    },
                )
        except Exception:
            # Reflection is best-effort and must never fail the run.
            pass

    def _judge_graph_findings(
        self,
        active: _ActiveRun,
        graph_findings: list[dict],
    ) -> None:
        if not graph_findings:
            return
        try:
            import json

            backend = self._json_backend_for(active)
            prompt = (
                "You are an independent security findings judge. "
                "Review the structured findings below and write 2-4 "
                "sentences assessing evidence quality, impact, and "
                "priority. Keep it under 300 words.\n\n"
                + json.dumps(
                    [
                        {
                            "category": finding.get("vuln_category"),
                            "endpoint": finding.get("endpoint"),
                            "severity": finding.get("severity"),
                            "status": finding.get("status"),
                        }
                        for finding in graph_findings[:20]
                    ],
                    ensure_ascii=True,
                )
            )
            verdict = ""
            for event in backend.stream(
                ContextView(
                    mission=prompt,
                    target_ref=active.agent_spec.target_ref,
                    observations=(),
                    remaining_budget=1,
                    context_blocks=ContextBlocks(),
                )
            ):
                if event.type == "model.finish":
                    if event.payload and event.payload.get("json"):
                        verdict = json.dumps(
                            event.payload["json"],
                            ensure_ascii=True,
                        )
                    elif event.text:
                        verdict += event.text
            if not verdict.strip():
                verdict = (
                    "LLM judge returned no structured verdict; "
                    "findings retained for manual review."
                )
            if active.events is not None:
                active.events.emit(
                    stream_id=active.run_id,
                    run_id=active.run_id,
                    event_type="finding.judged",
                    actor="agent-worker",
                    payload={
                        "finding_count": len(graph_findings),
                        "verdict": verdict.strip()[:1200],
                    },
                )
            for finding in graph_findings:
                try:
                    self._client.append_finding_note(
                        str(finding["finding_id"]),
                        f"LLM judge: {verdict.strip()[:800]}",
                    )
                except Exception:
                    continue
        except Exception:
            # Judge is best-effort and must never fail the run.
            print(
                f"judge failed run={active.run_id}",
                flush=True,
            )
            pass

    def _record_finding_memory(
        self,
        active: _ActiveRun,
        finding: dict,
    ) -> None:
        memory = self._memory_for_project(active.project_id)
        fact, inserted = memory.record(
            str(finding.get("endpoint") or active.agent_spec.target_ref),
            "finding",
            str(finding.get("vuln_category") or "finding"),
            target=active.agent_spec.target_ref,
            source_refs=(str(finding.get("finding_id")),),
            confidence=1.0,
            trust="project_observed",
        )
        if inserted and active.events is not None:
            active.events.emit(
                stream_id=active.run_id,
                run_id=active.run_id,
                event_type="memory.fact.appended",
                actor="agent-worker",
                payload={
                    "fact_id": fact.fact_id,
                    "subject": fact.subject,
                    "predicate": fact.predicate,
                    "value": fact.value,
                },
            )

    def _submit_connector_findings(
        self,
        active: _ActiveRun,
        observations: list[dict],
    ) -> None:
        hints = derive_connector_findings(
            observations,
            target_ref=active.agent_spec.target_ref,
        )
        for hint in hints:
            finding = self._client.submit_finding(
                active.run_id,
                **hint,
            )
            self._client.support_finding(finding["finding_id"])
            if active.events is not None:
                active.events.emit(
                    stream_id=active.run_id,
                    run_id=active.run_id,
                    event_type="finding.connector.candidate",
                    actor="agent-worker",
                    payload={
                        "finding_id": finding["finding_id"],
                        "vuln_category": finding["vuln_category"],
                        "endpoint": finding["endpoint"],
                        "source": str(hint.get("source") or "connector"),
                    },
                )

    def _submit_parsed_findings(self, active: _ActiveRun) -> None:
        collector = getattr(active.kernel, "observations", None)
        if not callable(collector):
            return
        seen = self._parsed_finding_seen.setdefault(active.run_id, set())
        for observation in collector():
            for parsed in observation.get("parsed_observations") or []:
                if not isinstance(parsed, dict):
                    continue
                if str(parsed.get("kind") or "") != "finding":
                    continue
                category = str(parsed.get("vuln_category") or "")
                if not category:
                    continue
                endpoint = str(
                    parsed.get("url")
                    or parsed.get("endpoint")
                    or active.agent_spec.target_ref
                )
                key = (category, endpoint)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    finding = self._client.submit_finding(
                        active.run_id,
                        target_ref=active.agent_spec.target_ref,
                        vuln_category=category,
                        endpoint=endpoint,
                        notes=(
                            f"source={str(parsed.get('source') or 'tool')} "
                            f"rule={str(parsed.get('rule_id') or '')} "
                            f"tool_call={str(observation.get('tool_call_id') or '')}"
                        ),
                        evidence={
                            "source_type": "external_scanner",
                            "artifact_refs": list(
                                observation.get("artifact_refs") or ()
                            ),
                            "action_ref": str(
                                observation.get("tool_call_id") or ""
                            ),
                            "confidence": float(parsed.get("confidence") or 0.6),
                            "parser_version": "1",
                        },
                    )
                    self._client.support_finding(finding["finding_id"])
                    self._record_finding_memory(active, finding)
                    if active.events is not None:
                        active.events.emit(
                            stream_id=active.run_id,
                            run_id=active.run_id,
                            event_type="finding.connector.candidate",
                            actor="agent-worker",
                            payload={
                                "finding_id": finding["finding_id"],
                                "vuln_category": finding["vuln_category"],
                                "endpoint": finding["endpoint"],
                                "source": str(parsed.get("source") or "tool"),
                            },
                        )
                except Exception:
                    continue

    def _submit_graph_findings(
        self,
        active: _ActiveRun,
        facts: tuple,
    ) -> list[dict]:
        """Materialize oracle-verified graph finding facts in the control plane."""
        rows: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for fact in facts:
            if getattr(fact, "predicate", "") != "finding":
                continue
            key = (
                str(fact.subject),
                str(fact.predicate),
                str(fact.value),
            )
            if key in seen:
                continue
            seen.add(key)
            metadata = getattr(fact, "metadata", None) or {}
            hint = {
                "target_ref": active.agent_spec.target_ref,
                "vuln_category": str(fact.value),
                "endpoint": str(fact.subject),
                "notes": (
                    f"graph fact={fact.fact_id} "
                    f"parser={metadata.get('source', '')} "
                    f"severity={metadata.get('severity', '')}"
                ),
                "evidence": {
                    "source_type": "structured_scan",
                    "artifact_refs": list(fact.source_refs),
                    "action_ref": fact.fact_id,
                    "confidence": float(fact.confidence or 0.8),
                    "parser_version": str(
                        metadata.get("parser_version") or "1"
                    ),
                },
            }
            finding = self._client.submit_finding(active.run_id, **hint)
            verified = False
            try:
                supported = self._client.support_finding(
                    finding["finding_id"]
                )
                if isinstance(supported, dict):
                    finding = {**finding, **supported}
                verified_response = self._client.verify_finding(
                    finding["finding_id"],
                    oracle="verified",
                )
                if isinstance(verified_response, dict):
                    finding = {**finding, **verified_response}
                verified = True
                if active.events is not None:
                    active.events.emit(
                        stream_id=active.run_id,
                        run_id=active.run_id,
                        event_type="finding.graph.verified",
                        actor="agent-worker",
                        payload={
                            "finding_id": finding["finding_id"],
                            "vuln_category": finding.get("vuln_category"),
                            "endpoint": finding.get("endpoint"),
                            "fact_id": fact.fact_id,
                        },
                    )
            except Exception as error:
                state = str(
                    finding.get("status")
                    or finding.get("state")
                    or ""
                )
                if state == "duplicate":
                    verified = True
                    try:
                        verified_response = self._client.verify_finding(
                            finding["finding_id"],
                            oracle="verified",
                        )
                        if isinstance(verified_response, dict):
                            finding = {**finding, **verified_response}
                    except Exception:
                        pass
                    finding = {
                        **finding,
                        "status": "verified",
                    }
                    if active.events is not None:
                        active.events.emit(
                            stream_id=active.run_id,
                            run_id=active.run_id,
                            event_type="finding.graph.duplicate",
                            actor="agent-worker",
                            payload={
                                "finding_id": finding["finding_id"],
                                "vuln_category": finding.get(
                                    "vuln_category"
                                ),
                                "endpoint": finding.get("endpoint"),
                            },
                        )
                else:
                    if active.events is not None:
                        active.events.emit(
                            stream_id=active.run_id,
                            run_id=active.run_id,
                            event_type="finding.graph.failed",
                            actor="agent-worker",
                            payload={
                                "finding_id": finding["finding_id"],
                                "error": (
                                    f"{type(error).__name__}: {error}"
                                ),
                            },
                        )
            if verified:
                try:
                    self._record_finding_memory(active, finding)
                except Exception as error:
                    if active.events is not None:
                        active.events.emit(
                            stream_id=active.run_id,
                            run_id=active.run_id,
                            event_type="memory.fact.failed",
                            actor="agent-worker",
                            payload={
                                "finding_id": finding["finding_id"],
                                "error": f"{type(error).__name__}: {error}",
                            },
                        )
            rows.append(finding)
        return rows

    def _collect_observations(self, runner: Any, run_id: str) -> list[dict]:
        collector = getattr(runner, "observations", None)
        if not callable(collector):
            return []
        try:
            records = collector()
        except Exception:
            return []
        proofs = {}
        proof_getter = getattr(runner, "replay_proofs", None)
        if callable(proof_getter):
            proofs = proof_getter()
        rows = []
        for record in records:
            data = (
                record.to_dict()
                if hasattr(record, "to_dict")
                else dict(record)
            )
            request_id = data.get("request_id")
            if request_id in proofs:
                data["replay_proof"] = proofs[request_id]
            data["auth_state"] = classify_auth_state(
                data.get("request_headers") or {}
            )
            rows.append(data)
        return rows

    def _mark_failed(self, run_id: str, error: Exception) -> None:
        print(
            f"run {run_id} failed: {type(error).__name__}: {error}",
            flush=True,
        )
        traceback.print_exc()
        try:
            self._client.finish(
                run_id,
                "failed",
                f"{run_id}:finish:error",
                stop_reason=type(error).__name__,
                summary=str(error),
            )
        except Exception:
            # Control plane may be unreachable; the run stays visible for review.
            pass

    def _run_graph_mode(
        self,
        run_id: str,
        agent_spec: AgentRunSpec,
        spec: dict,
        events: ControlPlaneEventSink,
    ) -> None:
        from services.research_service.graph_benchmark import (
            compare_single_vs_graph,
        )
        from services.research_service.models import Scenario

        scenario = Scenario(
            scenario_id=str(spec.get("scenario_id") or run_id),
            name=str(spec.get("name") or "graph"),
            target_ref=agent_spec.target_ref,
            mode="graph",
        )
        runs = int(spec.get("graph_runs", 1))
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="graph.started",
            actor="agent-worker",
            payload={"mode": "graph", "runs": runs},
        )
        single, graph, delta, recommendation = compare_single_vs_graph(
            scenario,
            runs=runs,
        )
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="graph.recommendation",
            actor="agent-worker",
            payload={
                "recommendation": recommendation,
                "verified_delta": delta["verified_avg"]["delta"],
                "cost_delta": delta["cost_avg"]["delta"],
                "duplicate_delta": delta["duplicate_actions_avg"]["delta"],
            },
        )
        summary = json.dumps(
            {
                "recommendation": recommendation,
                "single_runs": single.runs,
                "graph_runs": graph.runs,
                "verified_delta": delta["verified_avg"]["delta"],
                "cost_delta": delta["cost_avg"]["delta"],
                "duplicate_delta": delta["duplicate_actions_avg"]["delta"],
            },
            ensure_ascii=True,
        )
        self._client.finish(
            run_id,
            "succeeded",
            f"{run_id}:finish",
            stop_reason="graph.completed",
            summary=summary,
        )

    def _dispatch_remote_execution(
        self,
        run_id: str,
        agent_spec: AgentRunSpec,
        spec: dict,
        events: ControlPlaneEventSink,
    ) -> dict | None:
        """Dispatch a run to a configured execution node and record the event."""
        execution = spec.get("execution") or {}
        node_id = str(execution.get("node_id") or "").strip()
        if not node_id or node_id == "local":
            return None
        existing = [
            item
            for item in self._client.get_remote_results(node_id)
            if item.get("task_ref") == run_id
        ]
        if existing:
            return {"node_id": node_id, "result": existing[0]}
        payload = {
            "mode": "multi_role",
            "run_id": run_id,
            "mission_id": agent_spec.mission_id,
            "target_ref": agent_spec.target_ref,
            "role_template": spec.get("role_template"),
            "mission": agent_spec.mission,
        }
        tool = str(execution.get("tool") or "")
        if not tool:
            scanner_tools = spec.get("scanner_tools")
            if scanner_tools:
                tool = str(scanner_tools[0])
            else:
                tool = "shell.probe"
        payload["tool"] = tool
        payload["args"] = dict(execution.get("args") or {})
        payload["args"].setdefault("target", agent_spec.target_ref)
        if execution.get("command"):
            payload["command"] = str(execution["command"])
        try:
            lease = self._client.post_remote_dispatch(
                node_id,
                run_id,
                payload,
            )
        except Exception as error:
            events.emit(
                stream_id=run_id,
                run_id=run_id,
                event_type="run.remote_dispatch_failed",
                actor="agent-worker",
                payload={
                    "node_id": node_id,
                    "error": str(error),
                },
            )
            return None
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="run.remote_dispatched",
            actor="agent-worker",
            payload={
                "node_id": node_id,
                "lease_id": lease.get("lease", {}).get("lease_id", ""),
                "tool": tool,
            },
        )
        return {"node_id": node_id, "lease": lease, "payload": payload}

    def _wait_remote_result(
        self,
        node_id: str,
        run_id: str,
        *,
        timeout_seconds: float,
        poll_interval: float,
    ) -> dict | None:
        deadline = time.time() + max(0.0, timeout_seconds)
        while time.time() < deadline:
            try:
                results = self._client.get_remote_results(node_id)
            except WorkerControlError:
                results = []
            for item in results:
                if item.get("task_ref") == run_id:
                    return item
            time.sleep(max(0.0, poll_interval))
        return None

    def _ingest_remote_result(
        self,
        run_id: str,
        agent_spec: AgentRunSpec,
        spec: dict,
        events: ControlPlaneEventSink,
        node_id: str,
        result: dict,
    ) -> dict | None:
        """Turn a signed node result into run events and, when evidence is
        present, a control-plane finding."""
        payload = result.get("payload") or {}
        status = str(result.get("status") or "failed")
        stdout = str(payload.get("stdout") or "")
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="run.remote_result_received",
            actor="agent-worker",
            payload={
                "node_id": node_id,
                "status": status,
                "result_id": str(result.get("result_id") or ""),
                "stdout": stdout[-500:],
                "stderr": str(payload.get("stderr") or "")[-200:],
            },
        )
        hint = _finding_hint(spec)
        if status != "completed" or not hint:
            return None
        marker = str(hint.get("marker") or "")
        category = str(hint.get("category") or "")
        if not marker or marker not in stdout:
            return None
        evidence = {
            "source_type": "remote_node",
            "artifact_refs": [
                f"remote://node/{node_id}/result/"
                f"{result.get('result_id', '')}"
            ],
            "confidence": 0.8,
            "parser_version": "1",
        }
        finding = self._client.submit_finding(
            run_id,
            target_ref=agent_spec.target_ref,
            vuln_category=category,
            endpoint=agent_spec.target_ref,
            notes=(
                f"remote node={node_id} "
                f"result={result.get('result_id', '')}"
            ),
            evidence=evidence,
        )
        try:
            self._client.support_finding(finding["finding_id"])
            self._client.verify_finding(
                finding["finding_id"],
                oracle="remote_node",
            )
            events.emit(
                stream_id=run_id,
                run_id=run_id,
                event_type="finding.remote.verified",
                actor="agent-worker",
                payload={
                    "finding_id": finding["finding_id"],
                    "vuln_category": category,
                    "endpoint": agent_spec.target_ref,
                },
            )
        except Exception:
            state = ""
            try:
                latest = self._client.get_finding(
                    finding["finding_id"]
                )
                state = str(
                    latest.get("status")
                    or latest.get("state")
                    or ""
                )
            except Exception:
                pass
            if state == "duplicate":
                events.emit(
                    stream_id=run_id,
                    run_id=run_id,
                    event_type="finding.remote.duplicate",
                    actor="agent-worker",
                    payload={
                        "finding_id": finding["finding_id"],
                        "vuln_category": category,
                    },
                )
            else:
                events.emit(
                    stream_id=run_id,
                    run_id=run_id,
                    event_type="finding.remote.failed",
                    actor="agent-worker",
                    payload={
                        "finding_id": finding.get("finding_id", ""),
                        "vuln_category": category,
                    },
                )
        return finding

    def _run_multi_role_mode(
        self,
        run_id: str,
        agent_spec: AgentRunSpec,
        spec: dict,
        events: ControlPlaneEventSink,
        provider: dict | None = None,
        mission: dict | None = None,
    ) -> None:
        remote = self._dispatch_remote_execution(
            run_id,
            agent_spec,
            spec,
            events,
        )
        risk_registry = _load_tool_registry()
        fault_injector = FaultInjector.from_config(
            spec.get("fault_injection")
        )
        tool_schemas = _tool_schema_map(
            risk_registry,
            agent_spec.allowed_tools,
        )
        provider = provider or {}
        if not provider:
            default_resolver = getattr(
                self._client,
                "get_provider_default",
                None,
            )
            if default_resolver is not None:
                try:
                    default_provider = default_resolver()
                    if default_provider:
                        provider = default_provider
                except Exception:
                    pass
        provider_config = provider.get("config") or {}
        backend = _create_turn_backend(
            endpoint=(
                provider.get("endpoint")
                or agent_spec.provider_endpoint
            ),
            model=provider.get("model") or agent_spec.provider_model,
            api_key_ref=(
                provider.get("api_key_ref")
                or self._options.api_key_ref
            ),
            tool_schemas=tool_schemas,
            tool_descriptions=_tool_description_map(
                risk_registry,
                agent_spec.allowed_tools,
            ),
            provider_config=provider_config,
            options=self._options,
        )
        runner = self._runner_factory()
        runner = self._with_memory_runner(
            runner,
            mission or {},
            run_id,
        )
        broker = ToolBroker(
            runner,
            risk_resolver=(
                lambda name: (
                    risk_registry.get(name).risk_level
                    if risk_registry.get(name) is not None
                    else "L1"
                )
            ),
            max_risk_level=agent_spec.max_tool_risk,
        )
        raw_roles = spec.get("roles")
        if raw_roles:
            roles = tuple(AgentRole(**item) for item in raw_roles)
        elif (
            spec.get("role_template")
            and str(spec["role_template"])
            not in (
                "code_audit",
                "scanner_verify",
                "redteam_orchestration",
                "authz_matrix",
                "ssrf_callback",
                "graphql",
                "websocket",
                "webappsec",
            )
        ):
            template = self._client.get_role_template(
                str(spec["role_template"])
            )
            if template is None:
                raise WorkerControlError(
                    f"role template {spec['role_template']} not found"
                )
            raw = template.get("roles") or []
            if not raw:
                raise WorkerControlError(
                    f"role template {spec['role_template']} has no roles"
                )
            roles = tuple(AgentRole(**item) for item in raw)
        elif spec.get("role_template") == "scanner_verify":
            policy = scanner_verify_policy(spec)
            roles = scanner_verify_role_template(
                target_ref=agent_spec.target_ref,
                required_categories=tuple(
                    spec.get("required_categories", ())
                ),
                scanner_tools=tuple(
                    spec.get("scanner_tools")
                    or (
                        "zap.scan",
                        "caido.scan",
                        "burp.scan",
                    )
                ),
                **policy,
            )
        elif spec.get("role_template") == "redteam_orchestration":
            roles = redteam_orchestration_role_template(
                target_ref=agent_spec.target_ref,
                required_categories=tuple(
                    spec.get("required_categories", ())
                ),
                scanner_tools=tuple(
                    spec.get("scanner_tools")
                    or (
                        "nuclei.scan",
                        "web.nikto.scan",
                        "web.sqlmap.scan",
                    )
                ),
                min_severity=str(spec.get("min_severity") or ""),
                wall_clock_seconds=float(
                    spec.get("wall_clock_seconds") or 600
                ),
            )
        elif spec.get("role_template") == "code_audit":
            roles = code_audit_role_template(
                target_ref=agent_spec.target_ref,
                required_categories=tuple(
                    spec.get("required_categories", ())
                ),
                scanner_tools=tuple(
                    spec.get("code_tools")
                    or (
                        "code.sast.semgrep",
                        "code.secrets.detect",
                    )
                ),
                min_severity=str(spec.get("min_severity") or ""),
                wall_clock_seconds=float(
                    spec.get("wall_clock_seconds") or 120
                ),
            )
        elif spec.get("role_template") == "authz_matrix":
            roles = authz_matrix_role_template(
                target_ref=agent_spec.target_ref,
                allowed_tools=tuple(
                    spec.get("authz_tools")
                    or ("web.replay",)
                ),
                wall_clock_seconds=float(
                    spec.get("wall_clock_seconds") or 120
                ),
            )
        elif spec.get("role_template") == "ssrf_callback":
            roles = ssrf_callback_role_template(
                target_ref=agent_spec.target_ref,
                allowed_tools=tuple(
                    spec.get("ssrf_tools")
                    or (
                        "oast.create",
                        "oast.check",
                        "web.replay",
                    )
                ),
                wall_clock_seconds=float(
                    spec.get("wall_clock_seconds") or 120
                ),
            )
        elif spec.get("role_template") == "graphql":
            roles = graphql_role_template(
                target_ref=agent_spec.target_ref,
                allowed_tools=tuple(
                    spec.get("graphql_tools")
                    or ("web.graphql.test",)
                ),
                wall_clock_seconds=float(
                    spec.get("wall_clock_seconds") or 120
                ),
            )
        elif spec.get("role_template") == "websocket":
            roles = websocket_role_template(
                target_ref=agent_spec.target_ref,
                allowed_tools=tuple(
                    spec.get("websocket_tools")
                    or ("web.websocket.test",)
                ),
                wall_clock_seconds=float(
                    spec.get("wall_clock_seconds") or 120
                ),
            )
        elif spec.get("role_template") == "webappsec":
            roles = webappsec_role_template(
                target_ref=agent_spec.target_ref,
                wall_clock_seconds=float(
                    spec.get("wall_clock_seconds") or 60
                ),
            )
        else:
            roles = webappsec_role_template(
                target_ref=agent_spec.target_ref,
                wall_clock_seconds=float(
                    spec.get("wall_clock_seconds") or 60
                ),
            )

        role_tools = {
            tool
            for role in roles
            for tool in role.allowed_tools
        }
        if role_tools:
            agent_spec = replace(
                agent_spec,
                allowed_tools=tuple(
                    dict.fromkeys(
                        (
                            *agent_spec.allowed_tools,
                            *sorted(role_tools),
                        )
                    )
                ),
            )

        loop_runners: list = []

        def role_runner_factory(loop_spec):
            node_tools = _with_memory_tools(
                tuple(loop_spec.allowed_tools)
            )
            node_backend = _create_turn_backend(
                endpoint=(
                    provider.get("endpoint")
                    or agent_spec.provider_endpoint
                ),
                model=provider.get("model") or agent_spec.provider_model,
                api_key_ref=(
                    provider.get("api_key_ref")
                    or self._options.api_key_ref
                ),
                tool_schemas=_tool_schema_map(
                    risk_registry,
                    node_tools,
                ),
                tool_descriptions=_tool_description_map(
                    risk_registry,
                    node_tools,
                ),
                provider_config=provider_config,
                options=self._options,
            )
            self._assemble_context_blocks(
                mission or {},
                agent_spec,
                loop_spec=loop_spec,
            )
            self._emit_node_projection(events, run_id, loop_spec)
            runner = LoopRunner(
                loop_spec,
                TurnLoopModelAdapter(
                    node_backend,
                    target_ref=agent_spec.target_ref,
                    mission=agent_spec.mission,
                    summarizer=BackendSummarizer(node_backend),
                    context_blocks_provider=(
                        lambda: self._assemble_context_blocks(
                            mission or {},
                            agent_spec,
                            loop_spec=loop_spec,
                        ).blocks
                    ),
                ),
                BrokerLoopTool(
                    broker,
                    agent_spec,
                    tool_args=spec.get("tool_args") or {},
                    forced_tool_args=spec.get("forced_tool_args") or {},
                    fault_injector=fault_injector,
                    node_allowed_tools=node_tools,
                    argument_validator=(
                        lambda ref, args: (
                            validate_tool_arguments(
                                risk_registry.get(ref),
                                args,
                            )
                            if risk_registry.get(ref) is not None
                            else []
                        )
                    ),
                ),
                build_role_oracle(
                    loop_spec.profile,
                    loop_spec.budget,
                ),
            )
            loop_runners.append(runner)
            return runner

        emitted_human_gates: set[str] = set()

        def human_resolver(node_id: str, prompt: str) -> bool | None:
            if node_id not in emitted_human_gates:
                events.emit(
                    stream_id=run_id,
                    run_id=run_id,
                    event_type="graph.human.required",
                    actor="agent-worker",
                    payload={
                        "node_id": node_id,
                        "prompt": prompt,
                    },
                )
                emitted_human_gates.add(node_id)
            gates = self._client.get_human_gates(run_id)
            resolved = (gates.get("resolved") or {}).get(node_id)
            if resolved is None:
                return None
            return bool(resolved.get("approved"))

        fallback_cfg = spec.get("replan_fallback") or {}
        planners = [CandidateVerifierPlanner()]
        fallback_node = (
            _fallback_node_from_config(fallback_cfg)
            if fallback_cfg
            else None
        )
        if fallback_node is not None:
            planners.append(
                FailureDrivenReplanner(
                    fallback_node=fallback_node,
                    failed_node=str(
                        fallback_cfg.get("failed_node") or "scanner"
                    ),
                    target_node=fallback_cfg.get("target_node") or None,
                )
            )
        graph = RoleGraphRunner(
            roles=roles,
            runner_factory=role_runner_factory,
            graph_id=run_id,
            mission_ref=agent_spec.mission_id,
            target_ref=agent_spec.target_ref,
            planner=(
                ChainPlanner(tuple(planners))
                if not spec.get("disable_planner")
                else None
            ),
            store=GraphStore(
                str(self._runtime_dir / "mission-graphs.db")
            ),
            loop_checkpoint_store=self._loop_checkpoint_store,
            human_resolver=human_resolver,
            budget_overrides=spec.get("budget") or {},
            loop_overrides=spec.get("loop_profiles") or {},
        )
        print(
            f"graph-run start run={run_id} template="
            f"{spec.get('role_template')} roles={[r.role_id for r in roles]}",
            flush=True,
        )
        product_identity = _product_identity_for_spec(
            spec,
            self._runtime_dir,
        )
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="behavior.snapshot",
            actor="agent-worker",
            payload={
                "snapshot_id": agent_spec.behavior_snapshot,
                "product_identity_digest": product_identity,
                "config_hash": str(spec.get("config_hash") or ""),
                "provider": agent_spec.provider_model,
                "behavior_snapshot": agent_spec.behavior_snapshot,
            },
        )
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="graph.started",
            actor="agent-worker",
            payload={
                "mode": "multi_role",
                "roles": [
                    {
                        "role_id": role.role_id,
                        "budget": {
                            key: role.budget[key]
                            for key in (
                                "oracle",
                                "required_categories",
                                "min_severity",
                                "require_evidence",
                                "required_metadata_fields",
                                "dedupe",
                                "conflict_blocks",
                            )
                            if key in role.budget
                        },
                    }
                    for role in roles
                ],
            },
        )
        print(
            f"graph.run begin run={run_id}",
            flush=True,
        )
        result = graph.run()
        print(
            f"graph.run end run={run_id} waiting={result.waiting} "
            f"nodes={len(result.node_statuses)}",
            flush=True,
        )
        for runner in loop_runners:
            for loop_event in runner.events:
                try:
                    events.emit(
                        stream_id=run_id,
                        run_id=run_id,
                        event_type=loop_event.event_type,
                        actor="agent-worker",
                        payload={
                            "loop_id": loop_event.loop_id,
                            "iteration": loop_event.iteration,
                            "sequence": loop_event.sequence,
                            **(loop_event.payload or {}),
                        },
                    )
                except Exception:
                    pass
        if result.waiting:
            events.emit(
                stream_id=run_id,
                run_id=run_id,
                event_type="graph.waiting",
                actor="agent-worker",
                payload={"nodes": list(result.waiting_nodes)},
            )
            try:
                self._client.pause_run(
                    run_id,
                    f"{run_id}:human-gate:pause",
                )
            except WorkerControlError:
                pass
            return
        active = _ActiveRun(
            run_id=run_id,
            agent_spec=agent_spec,
            kernel=None,  # type: ignore[arg-type]
            runner=None,  # type: ignore[arg-type]
            backend=backend,
            provider_config=provider_config,
            provider_api_key_ref=provider.get("api_key_ref"),
            events=events,
            project_id=str(spec.get("project_id") or "default"),
        )
        graph_findings = self._submit_graph_findings(active, result.facts)
        self._judge_graph_findings(active, graph_findings)
        for node_id, status in result.node_statuses:
            events.emit(
                stream_id=run_id,
                run_id=run_id,
                event_type="graph.node.completed",
                actor="agent-worker",
                payload={"node_id": node_id, "status": status},
            )
        for handoff in result.handoffs:
            events.emit(
                stream_id=run_id,
                run_id=run_id,
                event_type="graph.handoff",
                actor="agent-worker",
                payload={
                    "from_node": handoff.from_node,
                    "to_node": handoff.to_node,
                    "fact_refs": list(handoff.fact_refs),
                    "evidence_refs": list(handoff.evidence_refs),
                    "summary": handoff.summary,
                },
            )
        metrics = result.metrics
        events.emit(
            stream_id=run_id,
            run_id=run_id,
            event_type="graph.completed",
            actor="agent-worker",
            payload={
                "handoffs": metrics.handoffs,
                "dead_letters": metrics.dead_letters,
                "duplicate_actions": metrics.duplicate_actions,
                "path_efficiency": metrics.path_efficiency,
                "finding_count": len(graph_findings),
                "verified_finding_count": sum(
                    1
                    for item in graph_findings
                    if item.get("status") == "verified"
                    or item.get("verified") is True
                ),
            },
        )
        remote_result = remote.get("result") if remote else None
        if remote and remote_result is None:
            execution = spec.get("execution") or {}
            remote_result = self._wait_remote_result(
                str(remote["node_id"]),
                run_id,
                timeout_seconds=float(
                    execution.get("wait_seconds") or 30
                ),
                poll_interval=float(
                    execution.get("poll_interval") or 2.0
                ),
            )
        remote_finding = None
        remote_status = ""
        if remote and remote_result is not None:
            remote_status = str(
                remote_result.get("status") or "failed"
            )
            remote_finding = self._ingest_remote_result(
                run_id,
                agent_spec,
                spec,
                events,
                str(remote["node_id"]),
                remote_result,
            )
        recommendation = ""
        comparison: dict = {}
        if spec.get("compare"):
            comparison = compare_single_vs_multi_role(
                target_ref=agent_spec.target_ref,
                runner_factory=role_runner_factory,
                roles=roles,
            )
            recommendation = str(comparison.get("recommendation") or "")
            events.emit(
                stream_id=run_id,
                run_id=run_id,
                event_type="graph.recommendation",
                actor="agent-worker",
                payload={
                    "recommendation": recommendation,
                    "single": comparison.get("single", {}),
                    "graph": comparison.get("graph", {}),
                },
            )
        verified_count = sum(
            1
            for item in graph_findings
            if item.get("status") == "verified"
            or item.get("verified") is True
        )
        if remote_finding is not None:
            verified_count += 1
        outcome = (
            "succeeded"
            if verified_count > 0
            or all(
                status == "succeeded"
                for _, status in result.node_statuses
            )
            else "failed"
        )
        execution = spec.get("execution") or {}
        if (
            remote is not None
            and remote_result is None
            and bool(execution.get("strict"))
        ):
            outcome = "failed"
        summary = json.dumps(
            {
                "mode": "multi_role",
                "roles": [role.role_id for role in roles],
                "node_statuses": list(result.node_statuses),
                "handoffs": metrics.handoffs,
                "dead_letters": metrics.dead_letters,
                "duplicate_actions": metrics.duplicate_actions,
                "path_efficiency": metrics.path_efficiency,
                "verified_finding_count": verified_count,
                "recommendation": recommendation,
                "remote_node": (
                    remote["node_id"] if remote else ""
                ),
                "remote_status": remote_status,
                "remote_finding_count": (
                    1 if remote_finding is not None else 0
                ),
            },
            ensure_ascii=True,
        )
        self._reflect_run(
            active,
            [
                {
                    "tool": str(fact.predicate or ""),
                    "vuln_category": str(
                        (getattr(fact, "metadata", None) or {}).get(
                            "vuln_category"
                        )
                        or ""
                    ),
                    "stdout": str(fact.value or "")[:800],
                }
                for fact in result.facts
            ],
        )
        self._client.finish(
            run_id,
            outcome,
            f"{run_id}:finish",
            stop_reason="multi_role.completed",
            summary=summary,
        )


def _provider_profile(endpoint: str) -> ProviderProfile:
    is_remote = not (
        endpoint.startswith("http://127.0.0.1")
        or endpoint.startswith("http://localhost")
    )
    allowed = (
        DataLabel.PUBLIC,
        DataLabel.PROJECT,
        DataLabel.SENSITIVE,
        DataLabel.SECRET,
    )
    return ProviderProfile(
        provider_id="worker",
        is_remote=is_remote,
        allowed_data_labels=allowed,
    )


def _embedding_backend_snapshot(config: dict) -> dict:
    embedding = config.get("embedding") or {}
    backend = str(embedding.get("backend") or "none")
    if backend != "none" and embedding.get("model"):
        return {
            "backend": backend,
            "model": str(embedding["model"]),
            "endpoint": str(embedding.get("endpoint") or ""),
        }
    return {"backend": "none", "model": ""}


def _vector_backend_snapshot(config: dict) -> dict:
    vector_config = config.get("vector_store") or {}
    backend = str(vector_config.get("type") or "sqlite")
    summary: dict = {"type": backend}
    if vector_config.get("url"):
        summary["url"] = str(vector_config["url"])
    if vector_config.get("database_url"):
        summary["database_url"] = str(vector_config["database_url"])
    return summary


def run_forever(
    client: ControlPlaneClient,
    options: WorkerOptions,
    *,
    runner_factory: Callable[[], Any] | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    worker = ControlPlaneRunWorker(
        client,
        runner_factory=runner_factory,
        options=options,
    )
    while not (stop_event is not None and stop_event.is_set()):
        try:
            worker.poll_once()
        except WorkerControlError as error:
            print(f"worker poll failed: {error}", flush=True)
        time.sleep(options.poll_interval_seconds)


@dataclass
class _ActiveRun:
    run_id: str
    agent_spec: AgentRunSpec
    kernel: AgentKernel
    runner: Any
    backend: Any | None = None
    provider_config: dict[str, Any] = field(default_factory=dict)
    provider_api_key_ref: str | None = None
    events: ControlPlaneEventSink | None = None
    finding_hint: dict | None = None
    thread: threading.Thread | None = None
    outcome: dict = field(default_factory=dict)
    local_paused: bool = False
    finished: bool = False
    project_id: str = "default"


def _compact_run_summary(
    run_id: str,
    observations: list[dict],
) -> str:
    tools = sorted(
        {
            str(observation.get("tool") or "")
            for observation in observations
            if observation.get("tool")
        }
    )
    categories = sorted(
        {
            str(observation.get("vuln_category") or "")
            for observation in observations
            if observation.get("vuln_category")
        }
    )
    return (
        f"run {run_id}: tools={','.join(tools) or '-'}; "
        f"findings={','.join(categories) or 'none'}"
    )


def _finding_hint(spec: dict) -> dict | None:
    marker = spec.get("expected_finding_marker")
    category = spec.get("vuln_category")
    if not marker or not category:
        return None
    return {"marker": marker, "category": category}


def _load_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    pack_dir = (
        Path(__file__).resolve().parents[2] / "deploy" / "toolpacks"
    )
    for path in sorted(pack_dir.glob("*.json")):
        try:
            registry.load_manifest(path)
        except Exception:
            continue
    return registry


def _tool_schema_map(
    registry: ToolRegistry,
    names: tuple[str, ...],
) -> dict[str, dict]:
    """Expose Tool Pack definitions to providers, with native fallbacks."""
    schemas: dict[str, dict] = {}
    for name in names:
        definition = registry.get(name)
        if definition is not None:
            schemas[name] = definition.schema
        elif name in DEFAULT_TOOL_SCHEMAS:
            schemas[name] = DEFAULT_TOOL_SCHEMAS[name]
    return schemas


def _tool_description_map(
    registry: ToolRegistry,
    names: tuple[str, ...],
) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for name in names:
        if name == "skill.read":
            descriptions[name] = (
                "Read a text resource from an included skill package "
                "(references, checklists, scripts or assets). Use the "
                "package-relative path shown in the skill projection."
            )
            continue
        if name == "memory.recall":
            descriptions[name] = (
                "Search the project fact memory by semantic or lexical "
                "query, subject or predicate. Use it before repeating an "
                "action to avoid duplicating prior work."
            )
            continue
        if name == "memory.record":
            descriptions[name] = (
                "Append a new observed fact to project memory. The fact "
                "must be supported by tool output or evidence. Trust is "
                "never promoted by this tool."
            )
            continue
        if name == "memory.status":
            descriptions[name] = (
                "Show project memory snapshot counts and recent run "
                "summaries."
            )
            continue
        definition = registry.get(name)
        if definition is None:
            continue
        parts = [definition.description or f"Veridix tool {name}"]
        if definition.examples:
            parts.append(
                "Examples:\n"
                + "\n".join(f"- {example}" for example in definition.examples)
            )
        descriptions[name] = "\n".join(parts)
    return descriptions


def _read_tool_environment_digest(runtime_dir: Path) -> str:
    path = Path(runtime_dir) / "tool-environment.json"
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return str(payload.get("digest") or "")
    except Exception:
        return ""


def _product_identity_for_spec(
    spec: dict,
    runtime_dir: Path,
) -> str:
    config = (
        spec.get("config")
        if isinstance(spec, dict)
        else {}
    )
    return product_identity_digest(
        config=dict(config or {}),
        tool_environment=load_tool_environment(runtime_dir),
        runtime_versions=load_runtime_versions(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="run control-plane agent worker")
    parser.add_argument("--control-url", default=os.environ.get("VERIDIX_CONTROL_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--endpoint", default=os.environ.get("VERIDIX_PROVIDER_ENDPOINT"))
    parser.add_argument("--model", default=os.environ.get("VERIDIX_PROVIDER_MODEL"))
    parser.add_argument("--api-key-ref")
    parser.add_argument("--worker-id", default="agent-worker")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--golden-finding", action="store_true")
    parser.add_argument("--streaming", action="store_true")
    parser.add_argument("--checkpoint-dir")
    parser.add_argument("--spool-limit", type=int, default=1000)
    parser.add_argument("--memory-db")
    parser.add_argument(
        "--runner",
        default=os.environ.get("VERIDIX_RUNNER", "fake"),
        help="runner kind: fake or docker",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    options = WorkerOptions(
        worker_id=args.worker_id,
        poll_interval_seconds=args.poll_interval,
        max_turns=args.max_turns,
        max_tokens=args.max_tokens,
        provider_endpoint=args.endpoint,
        provider_model=args.model,
        api_key_ref=args.api_key_ref,
        golden_finding=args.golden_finding,
        streaming=args.streaming,
        checkpoint_dir=args.checkpoint_dir,
        spool_limit=args.spool_limit,
        memory_db=args.memory_db,
    )
    client = ControlPlaneClient(args.control_url)
    runner_factory = None
    if args.runner != "fake":
        from services.agent_runtime.app.runner_factory import (
            build_worker_runner_factory,
        )

        runner_factory = build_worker_runner_factory(
            runner_kind=args.runner
        )
    worker = ControlPlaneRunWorker(
        client,
        options=options,
        runner_factory=runner_factory,
    )
    if args.once:
        print(json.dumps({"claimed": worker.poll_once()}, ensure_ascii=False))
        return 0
    run_forever(client, options, runner_factory=runner_factory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
