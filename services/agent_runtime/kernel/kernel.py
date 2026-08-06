from __future__ import annotations

from dataclasses import replace
import time
from typing import Any, Callable

from .contracts import (
    AgentRunSpec,
    Checkpoint,
    ContextBlocks,
    ContextView,
    ExecutionRequest,
    RunStatus,
    ToolCall,
)
from .context import (
    ContentPiece,
    ContentTrustEngine,
    DataLabel,
    DataReleaseDecider,
    ProviderProfile,
    SourceKind,
    TrustedContent,
    TrustLevel,
)
from .context_trimming import trim_observations
from .ports import CheckpointStorePort, EventSinkPort, ToolBrokerPort, TurnBackendPort


class AgentKernel:
    def __init__(
        self,
        spec: AgentRunSpec,
        backend: TurnBackendPort,
        broker: ToolBrokerPort,
        events: EventSinkPort,
        checkpoints: CheckpointStorePort,
        max_retries_on_rate_limit: int = 1,
        max_retries_on_timeout: int = 1,
        max_retries_on_unavailable: int = 1,
        max_retry_wait_seconds: float = 30.0,
        content_trust: ContentTrustEngine | None = None,
        data_release: DataReleaseDecider | None = None,
        provider_profile: ProviderProfile | None = None,
        context_provider: Callable[[], ContextBlocks] | None = None,
        summarizer: (
            Callable[[tuple[dict[str, Any], ...]], str] | None
        ) = None,
    ) -> None:
        self._spec = spec
        self._backend = backend
        self._broker = broker
        self._events = events
        self._checkpoints = checkpoints
        self._max_retries_on_rate_limit = max_retries_on_rate_limit
        self._max_retries_on_timeout = max_retries_on_timeout
        self._max_retries_on_unavailable = max_retries_on_unavailable
        self._max_retry_wait_seconds = max_retry_wait_seconds
        self._content_trust = content_trust or ContentTrustEngine()
        self._data_release = data_release or DataReleaseDecider()
        self._provider_profile = provider_profile or ProviderProfile(
            provider_id="local",
            is_remote=False,
            allowed_data_labels=(
                DataLabel.PUBLIC,
                DataLabel.PROJECT,
                DataLabel.SENSITIVE,
                DataLabel.SECRET,
            ),
        )
        self._context_provider = context_provider
        self._summarizer = summarizer
        self._status = RunStatus.QUEUED
        self._observations: list[dict] = []
        self._stop = False
        self._action_counter = 0
        self._turns_done = 0
        self._last_usage: dict[str, Any] | None = None

    @property
    def backend(self) -> TurnBackendPort:
        return self._backend

    def _emit(self, event_type: str, payload: dict | None = None) -> None:
        self._events.emit(
            stream_id=self._spec.run_id,
            run_id=self._spec.run_id,
            event_type=event_type,
            actor="agent-worker",
            payload=payload or {},
        )

    def _checkpoint(self) -> None:
        self._checkpoints.save(
            Checkpoint(
                run_id=self._spec.run_id,
                cursor=self._events.latest_sequence(self._spec.run_id),
                state={
                    "status": self._status.value,
                    "observations": list(self._observations),
                    "executed_keys": self._broker.snapshot_keys(),
                    "executions": self._broker.snapshot_outcomes(),
                    "action_counter": self._action_counter,
                    "turns_done": self._turns_done,
                    "last_usage": self._last_usage,
                },
                transcript=tuple(self._observations),
            )
        )

    def start(self) -> RunStatus:
        self._status = RunStatus.RUNNING
        self._emit("run.started", {"behavior_snapshot": self._spec.behavior_snapshot})
        self._checkpoint()
        return self._status

    def submit(self, user_input: str) -> RunStatus:
        self._emit("run.submitted", {"user_input": user_input})
        return self._run_loop()

    def apply_user_input(self, user_input: str) -> None:
        text = user_input.strip()
        if not text:
            return
        marker = "\n\n[用户追加指令] "
        base = self._spec.mission.split(marker, 1)[0]
        self._spec = replace(
            self._spec,
            mission=f"{base}{marker}{text}",
        )

    def pause(self) -> RunStatus:
        self._stop = True
        if self._status == RunStatus.RUNNING:
            self._status = RunStatus.PAUSED
            self._emit("run.paused", {"reason": "user_requested"})
            self._checkpoint()
        return self._status

    def resume(self, user_input: str | None = None) -> RunStatus:
        checkpoint = self._checkpoints.load(self._spec.run_id)
        if checkpoint is None:
            raise RuntimeError("no checkpoint to resume")
        return self.resume_from_checkpoint(checkpoint, user_input)

    def resume_from_checkpoint(
        self,
        checkpoint: Checkpoint,
        user_input: str | None = None,
    ) -> RunStatus:
        state = checkpoint.state
        self._status = RunStatus(state["status"])
        self._observations = (
            list(checkpoint.transcript)
            if checkpoint.transcript
            else list(state.get("observations") or ())
        )
        self._broker.restore_outcomes(state.get("executions") or {})
        self._broker.restore_keys(state["executed_keys"])
        self._action_counter = int(state.get("action_counter", 0))
        self._turns_done = int(state.get("turns_done", 0))
        self._last_usage = state.get("last_usage")
        self._stop = False
        self.apply_user_input(user_input or "")
        self._emit(
            "run.resumed",
            {"cursor": checkpoint.cursor, "user_input": user_input or ""},
        )
        if self._status in (RunStatus.ATTENTION_REQUIRED, RunStatus.CANCELLED):
            return self._status
        self._status = RunStatus.RUNNING
        return self._run_loop()

    def cancel(self) -> RunStatus:
        self._stop = True
        self._status = RunStatus.CANCELLED
        self._emit("run.cancelled", {"reason": "user_requested"})
        self._checkpoint()
        return self._status

    def _run_loop(self) -> RunStatus:
        deadline = self._budget_deadline()
        unlimited_turns = self._spec.budget_policy == "continue"
        while (
            self._turns_done < self._spec.max_turns or unlimited_turns
        ) and not self._stop:
            if deadline is not None and time.monotonic() >= deadline:
                self._status = RunStatus.PAUSED
                self._emit(
                    "run.budget_exhausted",
                    {"reason": "wall_clock_budget_exhausted"},
                )
                self._checkpoint()
                return self._status
            self.run_turn()
        if self._status == RunStatus.RUNNING and not unlimited_turns:
            self._status = RunStatus.PAUSED
            self._emit(
                "run.budget_exhausted",
                {
                    "reason": "turn_budget_exhausted",
                    "policy": self._spec.budget_policy,
                },
            )
            self._checkpoint()
        return self._status

    def _budget_deadline(self) -> float | None:
        seconds = self._spec.wall_clock_seconds
        if seconds is None:
            return None
        if seconds < 0:
            return None
        return time.monotonic() + seconds

    def run_turn(self) -> RunStatus:
        if self._stop or (
            self._turns_done >= self._spec.max_turns
            and self._spec.budget_policy != "continue"
        ):
            return self._status
        self._turns_done += 1
        self._emit("model.turn.started", {"turn": self._turns_done})
        observations, heuristic, removed = trim_observations(
            self._released_observations(),
            max_context_tokens=self._effective_max_context_tokens(),
        )
        summary = heuristic
        if removed and self._summarizer is not None:
            try:
                generated = self._summarizer(removed)
                if generated:
                    summary = generated
            except Exception:
                summary = heuristic
        blocks = (
            self._context_provider() if self._context_provider else None
        )
        if summary:
            blocks = replace(
                blocks or ContextBlocks(),
                summaries=(*(blocks.summaries if blocks else ()), summary),
            )
        context = ContextView(
            mission=self._spec.mission,
            target_ref=self._spec.target_ref,
            observations=observations,
            remaining_budget=max(0, self._spec.max_turns - self._turns_done),
            context_blocks=blocks,
        )
        finished = False
        attempts = 0
        while True:
            try:
                for model_event in self._backend.stream(context):
                    if self._stop:
                        break
                    if model_event.type == "model.usage" and isinstance(
                        model_event.payload,
                        dict,
                    ):
                        self._last_usage = dict(model_event.payload)
                    if model_event.type == "model.finish":
                        finished = True
                    if model_event.type == "model.delta" and model_event.text:
                        self._emit("model.delta", {"text": model_event.text})
                    if model_event.tool_call is not None:
                        self._handle_tool_call(model_event)
                break
            except Exception as error:
                category = getattr(error, "category", None)
                max_retries = {
                    "provider_rate_limit": self._max_retries_on_rate_limit,
                    "provider_timeout": self._max_retries_on_timeout,
                    "provider_unavailable": self._max_retries_on_unavailable,
                }.get(category, 0)
                if not (category and attempts < max_retries):
                    raise
                retry_after = getattr(error, "retry_after_seconds", None)
                self._emit(
                    "model.retry",
                    {
                        "attempt": attempts + 1,
                        "category": category,
                        "retry_after_seconds": retry_after,
                    },
                )
                wait = min(
                    float(
                        retry_after
                        if retry_after is not None
                        else min(
                            self._max_retry_wait_seconds,
                            2 ** attempts,
                        )
                    ),
                    self._max_retry_wait_seconds,
                )
                if wait > 0:
                    time.sleep(wait)
                attempts += 1
        if self._status == RunStatus.PAUSED:
            self._emit("run.paused", {"reason": "pause_requested"})
            self._checkpoint()
            return self._status
        if finished:
            self._status = RunStatus.SUCCEEDED
            self._stop = True
            self._emit("run.succeeded", {"stop_reason": "model.finish"})
            self._checkpoint()
            return self._status
        self._checkpoint()
        return self._status

    def _effective_max_context_tokens(self) -> int:
        if not self._last_usage:
            return self._spec.max_context_tokens
        prompt_tokens = int(self._last_usage.get("prompt_tokens") or 0)
        if prompt_tokens >= self._spec.max_context_tokens - 4096:
            return max(4096, self._spec.max_context_tokens - 8192)
        return self._spec.max_context_tokens

    def _handle_tool_call(self, event: ModelEvent) -> None:
        call = event.tool_call
        if call is None:
            return
        self._emit("tool.proposed", {"tool": call.name, "arguments": call.arguments})
        decision = self._broker.authorize(call, self._spec)
        if not decision.allowed:
            self._emit(
                "tool.denied",
                {"tool": call.name, "rule": decision.rule, "reason": decision.explanation},
            )
            return
        self._emit("tool.authorized", {"tool": call.name, "rule": decision.rule})
        if call.name == "run.finish":
            self._status = RunStatus.SUCCEEDED
            self._stop = True
            self._emit(
                "run.succeeded",
                {
                    "stop_reason": "run.finish",
                    "summary": str(call.arguments.get("summary", "")),
                },
            )
            self._checkpoint()
            return
        self._action_counter += 1
        request = ExecutionRequest(
            action_id=f"action_{self._spec.run_id}_{self._action_counter}",
            run_id=self._spec.run_id,
            tool_ref=call.name,
            input=call.arguments,
            idempotency_key=(
                f"{self._spec.run_id}:{call.id}"
                if call.id
                else f"{self._spec.run_id}:{call.name}:{self._action_counter}"
            ),
        )
        self._emit("tool.started", {"action_id": request.action_id, "tool": call.name})
        outcome = self._broker.execute(request)
        result = outcome.result
        if result.status != "completed":
            self._emit(
                "tool.failed",
                {
                    "action_id": request.action_id,
                    "tool": call.name,
                    "exit_code": result.exit_code,
                    "stderr": result.stderr,
                },
            )
            self._checkpoint()
            return
        if result.side_effect_state == "unknown":
            self._status = RunStatus.ATTENTION_REQUIRED
            self._emit(
                "side_effect_unknown",
                {
                    "action_id": request.action_id,
                    "tool": call.name,
                    "recovery": ["reobserve", "abort"],
                },
            )
            self._checkpoint()
            self._stop = True
            return
        trusted = self._content_trust.classify(
            ContentPiece(
                piece_id=f"{request.action_id}:stdout",
                source_kind=SourceKind.TOOL_OUTPUT,
                content=result.stdout,
                data_label=DataLabel.SENSITIVE,
                source_ref=f"artifact://{request.action_id}/stdout",
            )
        )
        if trusted.trust_level == TrustLevel.ADVERSARIAL:
            self._emit(
                "content.trust_denied",
                {
                    "piece_id": trusted.piece.piece_id,
                    "pattern": (
                        trusted.injection.pattern
                        if trusted.injection is not None
                        else "unknown"
                    ),
                    "action_id": request.action_id,
                },
            )
        stdout = (
            "[adversarial content isolated]"
            if trusted.trust_level == TrustLevel.ADVERSARIAL
            else result.stdout
        )
        self._observations.append(
            {
                "tool": call.name,
                "arguments": call.arguments,
                "tool_call_id": call.id,
                "reasoning_content": event.reasoning_content,
                "stdout": stdout,
                "artifact_refs": list(result.artifact_refs),
                "replayed": outcome.replayed,
                "trust": trusted.trust_level.value,
                "data_label": DataLabel.SENSITIVE.value,
                "parsed_observations": list(result.observations),
            }
        )
        self._emit(
            "tool.completed",
            {"action_id": request.action_id, "tool": call.name, "replayed": outcome.replayed},
        )
        self._emit(
            "observation.ingested",
            {
                "tool": call.name,
                "arguments": call.arguments,
                "tool_call_id": call.id,
                "reasoning_content": event.reasoning_content,
                "stdout": stdout,
                "artifact_refs": list(result.artifact_refs),
                "trust": trusted.trust_level.value,
                "parsed_observations": list(result.observations),
            },
        )
        if call.name == "run.finish":
            self._status = RunStatus.SUCCEEDED
            self._emit("run.succeeded", {"stop_reason": "run.finish"})
            self._stop = True
        self._checkpoint()

    def _released_observations(self) -> list[dict]:
        released: list[dict] = []
        for observation in self._observations:
            if observation.get("trust") == TrustLevel.ADVERSARIAL.value:
                continue
            decision = self._data_release.decide(
                TrustedContent(
                    piece=ContentPiece(
                        piece_id=str(
                            observation.get("tool_call_id") or "observation"
                        ),
                        source_kind=SourceKind.TOOL_OUTPUT,
                        content=str(observation.get("stdout", "")),
                        data_label=DataLabel(
                            observation.get(
                                "data_label",
                                DataLabel.SENSITIVE.value,
                            )
                        ),
                    ),
                    trust_level=TrustLevel(
                        observation.get(
                            "trust",
                            TrustLevel.RETRIEVED_UNTRUSTED.value,
                        )
                    ),
                ),
                self._provider_profile,
            )
            if decision.decision == "deny":
                self._emit(
                    "data.release",
                    {"decision": "deny", "reason": decision.reason},
                )
                continue
            if decision.decision in ("redact", "replace_with_ref"):
                self._emit(
                    "data.release",
                    {
                        "decision": decision.decision,
                        "reason": decision.reason,
                    },
                )
            released.append({**observation, "stdout": decision.content})
        return released

    def observations(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._observations)
