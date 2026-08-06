from __future__ import annotations

import json
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Iterable

from .contracts import (
    ActionProposal,
    Checkpoint,
    CoverageRecord,
    FactRecord,
    LoopEvent,
    LoopMetrics,
    LoopResult,
    LoopSpec,
    LoopState,
    LoopToolResult,
    ModelDecision,
    OracleResult,
)
from .ports import LoopModelPort, LoopToolPort, OraclePort


class LoopRunner:
    def __init__(
        self,
        spec: LoopSpec,
        model: LoopModelPort,
        tools: LoopToolPort,
        oracle: OraclePort,
        *,
        events: list[LoopEvent] | None = None,
        checkpoint_store=None,
        checkpoint_ref: str | None = None,
    ) -> None:
        self._spec = spec
        self._model = model
        self._tools = tools
        self._oracle = oracle
        self._events: list[LoopEvent] = events if events is not None else []
        self._checkpoint_store = checkpoint_store
        self._checkpoint_ref = checkpoint_ref or self._spec.loop_id
        self._seq = 0
        self._facts: list[FactRecord] = []
        self._evidence_refs: set[str] = set()
        self._observed: set[str] = set()
        self._known: tuple[str, ...] = ()
        self._hypotheses: tuple[str, ...] = ()
        self._iterations = 0
        self._status = "created"
        self._stop_reason: str | None = None
        self._oracle_result: OracleResult | None = None
        self._retry_counts: dict[str, int] = {}
        self._tool_failures: dict[str, int] = {}
        self._key_counts: dict[str, int] = {}
        self._seen_keys: set[str] = set()
        self._first_seen_iteration: dict[str, int] = {}
        self._tool_calls = 0
        self._tool_errors = 0
        self._denied = 0
        self._retries = 0
        self._replans = 0
        self._duplicate_actions = 0
        self._token_estimate = 0
        self._last_progress_iteration = 0
        self._last_tool_observations: tuple[dict[str, Any], ...] = ()
        self._observation_history: list[dict[str, Any]] = []
        self._budget_warned: set[str] = set()
        self._metrics: LoopMetrics | None = None
        self._deadline = self._resolve_deadline()
        self._max_retries = int(
            self._spec.budget.get("retry_transient", 2) or 0
        )
        self._tool_failure_replan_threshold = int(
            self._spec.budget.get("tool_failure_replan_threshold", 2) or 2
        )

    @property
    def events(self) -> list[LoopEvent]:
        return self._events

    @property
    def facts(self) -> tuple[FactRecord, ...]:
        return tuple(self._facts)

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(sorted(self._evidence_refs))

    @property
    def coverage(self) -> CoverageRecord:
        return CoverageRecord(
            observed=tuple(sorted(self._observed)),
            known=self._known,
        )

    def _emit(
        self,
        event_type: str,
        *,
        iteration: int | None = None,
        payload: dict | None = None,
    ) -> None:
        self._seq += 1
        self._events.append(
            LoopEvent(
                loop_id=self._spec.loop_id,
                event_type=event_type,
                sequence=self._seq,
                iteration=iteration,
                payload=payload or {},
            )
        )

    def _checkpoint(self) -> None:
        if self._checkpoint_store is None:
            return
        self._checkpoint_store.save(
            Checkpoint(
                run_id=self._checkpoint_ref,
                cursor=self._seq,
                state={
                    "schema": "veridix.loop.v1",
                    "events": [
                        _loop_event_to_dict(event)
                        for event in self._events
                    ],
                    "facts": [
                        _fact_to_dict(fact) for fact in self._facts
                    ],
                    "evidence_refs": sorted(self._evidence_refs),
                    "observed": sorted(self._observed),
                    "known": list(self._known),
                    "hypotheses": list(self._hypotheses),
                    "iterations": self._iterations,
                    "status": self._status,
                    "stop_reason": self._stop_reason,
                    "oracle_result": _oracle_to_dict(self._oracle_result),
                    "retry_counts": dict(self._retry_counts),
                    "tool_failures": dict(self._tool_failures),
                    "key_counts": dict(self._key_counts),
                    "seen_keys": sorted(self._seen_keys),
                    "first_seen_iteration": dict(
                        self._first_seen_iteration
                    ),
                    "tool_calls": self._tool_calls,
                    "tool_errors": self._tool_errors,
                    "denied": self._denied,
                    "retries": self._retries,
                    "replans": self._replans,
                    "duplicate_actions": self._duplicate_actions,
                    "token_estimate": self._token_estimate,
                    "last_progress_iteration": self._last_progress_iteration,
                    "last_tool_observations": [
                        dict(item) for item in self._last_tool_observations
                    ],
                    "observation_history": [
                        dict(item) for item in self._observation_history
                    ],
                    "budget_warned": sorted(self._budget_warned),
                },
                transcript=tuple(
                    dict(item) for item in self._observation_history
                ),
            )
        )

    def restore_checkpoint(self, checkpoint: Checkpoint) -> None:
        state = checkpoint.state
        self._seq = int(checkpoint.cursor)
        self._events = [
            _loop_event_from_dict(item)
            for item in state.get("events", [])
        ]
        self._facts = [
            _fact_from_dict(item) for item in state.get("facts", [])
        ]
        self._evidence_refs = set(state.get("evidence_refs", ()))
        self._observed = set(state.get("observed", ()))
        self._known = tuple(state.get("known", ()))
        self._hypotheses = tuple(state.get("hypotheses", ()))
        self._iterations = int(state.get("iterations", 0))
        self._status = str(state.get("status", "running"))
        self._stop_reason = state.get("stop_reason")
        self._oracle_result = _oracle_from_dict(
            state.get("oracle_result")
        )
        self._retry_counts = {
            str(key): int(value)
            for key, value in state.get("retry_counts", {}).items()
        }
        self._tool_failures = {
            str(key): int(value)
            for key, value in state.get("tool_failures", {}).items()
        }
        self._key_counts = {
            str(key): int(value)
            for key, value in state.get("key_counts", {}).items()
        }
        self._seen_keys = set(state.get("seen_keys", ()))
        self._first_seen_iteration = {
            str(key): int(value)
            for key, value in state.get(
                "first_seen_iteration",
                {},
            ).items()
        }
        self._tool_calls = int(state.get("tool_calls", 0))
        self._tool_errors = int(state.get("tool_errors", 0))
        self._denied = int(state.get("denied", 0))
        self._retries = int(state.get("retries", 0))
        self._replans = int(state.get("replans", 0))
        self._duplicate_actions = int(state.get("duplicate_actions", 0))
        self._token_estimate = int(state.get("token_estimate", 0))
        self._last_progress_iteration = int(
            state.get("last_progress_iteration", 0)
        )
        self._last_tool_observations = tuple(
            dict(item)
            for item in state.get("last_tool_observations", ())
        )
        self._observation_history = [
            dict(item)
            for item in (
                checkpoint.transcript
                or state.get("observation_history", ())
            )
        ]
        self._budget_warned = set(state.get("budget_warned", ()))
        self._deadline = self._resolve_deadline()

    def attach_checkpoint_store(self, store, *, checkpoint_ref: str | None = None) -> None:
        self._checkpoint_store = store
        if checkpoint_ref:
            self._checkpoint_ref = checkpoint_ref

    def _state(self) -> LoopState:
        return LoopState(
            loop_id=self._spec.loop_id,
            spec_ref=self._spec.loop_id,
            iteration=self._iterations,
            status=self._status,
            pending_observations=tuple(sorted(self._observed)),
            hypotheses=self._hypotheses,
            evidence_refs=self.evidence_refs,
            coverage=self.coverage,
            retry_counts=dict(self._retry_counts),
            last_tool_observations=self._last_tool_observations,
            observation_history=tuple(self._observation_history),
        )

    def run(
        self,
        *,
        known_endpoints: Iterable[str] = (),
        hypotheses: Iterable[str] = (),
        resumed: bool = False,
    ) -> LoopResult:
        if not resumed:
            self._known = tuple(known_endpoints)
            self._hypotheses = tuple(hypotheses)
            self._status = "running"
            self._emit("loop.started")
        else:
            self._status = "running"
            self._stop_reason = None
            self._emit(
                "loop.resumed",
                payload={"checkpoint_ref": self._checkpoint_ref},
            )

        while self._iterations < self._spec.max_iterations:
            if self._deadline is not None and time.monotonic() >= self._deadline:
                self._stop_budget("wall_clock_budget_exhausted")
                break
            self._check_budgets()
            if self._status != "running":
                break
            if self._no_progress():
                self._status = "inconclusive"
                self._stop_reason = "no_progress"
                self._replans += 1
                self._emit(
                    "loop.replan.suggested",
                    iteration=self._iterations,
                    payload={"reason": "no_progress"},
                )
                break
            self._iterations += 1
            self._emit(
                "loop.iteration.started",
                iteration=self._iterations,
            )
            decision = self._model.propose(
                self._state(),
                {"mission": "wp04-fixture"},
            )
            if decision.kind == "wait":
                self._status = "waiting"
                self._stop_reason = "wait_requested"
                break
            if decision.kind == "finish":
                self._emit(
                    "loop.finish.proposed",
                    iteration=self._iterations,
                )
                self._evaluate_oracle()
                if self._status == "running":
                    self._status = "inconclusive"
                    self._stop_reason = "oracle_not_verified"
                break

            proposals = decision.actions or (
                (decision.action,) if decision.action is not None else ()
            )
            if not proposals:
                raise RuntimeError("action decision without proposal")
            stop = False
            if (
                len(proposals) > 1
                and self._spec.budget.get("parallel_tool_calls")
            ):
                stop = self._run_proposals_parallel(proposals)
            else:
                for proposal in proposals:
                    result, should_stop = self._handle_proposal(proposal)
                    if should_stop:
                        stop = True
                        break
                    if result is None or result.status == "denied":
                        continue
                    if result.status == "finished":
                        self._evaluate_oracle()
                        if self._status == "running":
                            self._status = "inconclusive"
                            self._stop_reason = "oracle_not_verified"
                        self._emit(
                            "loop.iteration.ended",
                            iteration=self._iterations,
                            payload={"status": self._status},
                        )
                        stop = True
                        break
            if not stop:
                self._evaluate_oracle()
                self._emit(
                    "loop.iteration.ended",
                    iteration=self._iterations,
                    payload={"status": self._status},
                )
                if self._status in (
                    "succeeded",
                    "failed",
                    "waiting",
                    "inconclusive",
                ):
                    stop = True
            if stop:
                break

        if self._status == "running":
            self._status = "inconclusive"
            self._stop_reason = "budget_exhausted"
        return LoopResult(
            status=self._status,
            facts=tuple(self._facts),
            evidence_refs=self.evidence_refs,
            candidate_findings=tuple(self._hypotheses),
            coverage=self.coverage,
            stop_reason=self._stop_reason,
            oracle_result=self._oracle_result,
            metrics=self._finish_metrics(),
        )

    def _resolve_deadline(self) -> float | None:
        if "wall_clock_seconds" not in self._spec.budget:
            return None
        seconds = float(self._spec.budget.get("wall_clock_seconds", 0) or 0)
        if seconds < 0:
            return None
        return time.monotonic() + seconds

    def _stop_budget(self, reason: str) -> None:
        self._status = "inconclusive"
        self._stop_reason = "budget_exhausted"
        self._emit(
            "loop.budget.exhausted",
            payload={"reason": reason},
        )

    def _check_budgets(self) -> None:
        policy = str(self._spec.budget.get("policy", "relaxed"))
        for name, used, limit in (
            (
                "tool_calls",
                self._tool_calls,
                int(self._spec.budget.get("tool_calls", 0) or 0),
            ),
            (
                "tokens",
                self._token_estimate,
                int(self._spec.budget.get("tokens", 0) or 0),
            ),
        ):
            if limit <= 0 or used < limit:
                continue
            if name in self._budget_warned:
                continue
            self._budget_warned.add(name)
            self._emit(
                "loop.budget.exhausted",
                iteration=self._iterations,
                payload={
                    "reason": f"{name}_budget_exhausted",
                    "used": used,
                    "limit": limit,
                    "policy": policy,
                },
            )
            if policy == "strict":
                self._status = "inconclusive"
                self._stop_reason = "budget_exhausted"

    def _no_progress(self) -> bool:
        if self._iterations == 0:
            return False
        limit = int(
            self._spec.budget.get("max_no_progress_iterations", 8) or 8
        )
        return self._iterations - self._last_progress_iteration >= limit

    def _prepare_proposal(
        self,
        proposal: ActionProposal,
    ) -> tuple[str, LoopToolResult | None]:
        self._emit(
            "loop.action.proposed",
            iteration=self._iterations,
            payload={"tool": proposal.tool_ref, "input": proposal.input},
        )
        key = f"{proposal.tool_ref}:{json.dumps(proposal.input, sort_keys=True)}"
        self._key_counts[key] = self._key_counts.get(key, 0) + 1
        if key in self._seen_keys:
            self._duplicate_actions += 1
        else:
            self._first_seen_iteration[key] = self._iterations
        self._seen_keys.add(key)
        if self._key_counts[key] >= 3 and not self._observed:
            self._status = "inconclusive"
            self._stop_reason = "model_looping"
            self._replans += 1
            self._emit(
                "loop.replan.suggested",
                iteration=self._iterations,
                payload={"reason": "model_looping"},
            )
            return "stop", None
        if (
            key in self._first_seen_iteration
            and self._key_counts[key] >= 2
            and self._last_progress_iteration
            <= self._first_seen_iteration.get(key, 0)
        ):
            self._emit(
                "loop.action.duplicate_skipped",
                iteration=self._iterations,
                payload={
                    "tool": proposal.tool_ref,
                    "attempts": self._key_counts[key],
                },
            )
            synthetic = LoopToolResult(
                status="completed",
                observations=(
                    {
                        "tool": proposal.tool_ref,
                        "stdout": "",
                        "note": "duplicate_action_skipped",
                        "reasoning_content": proposal.reasoning or "",
                    },
                ),
                facts=(),
                evidence_refs=(),
            )
            return "skip", synthetic
        return "execute", None

    def _aggregate_result(
        self,
        proposal: ActionProposal,
        result: LoopToolResult | None,
    ) -> bool:
        if result is None or result.status == "denied":
            return False
        if result.status == "finished":
            return True
        if self._status in ("failed", "waiting", "inconclusive"):
            self._emit(
                "loop.iteration.ended",
                iteration=self._iterations,
                payload={"status": self._status},
            )
            return True
        progress = False
        for observation in result.observations:
            endpoint = observation.get("endpoint")
            observation_kind = str(
                observation.get("kind") or ""
            )
            self._observation_history.append(dict(observation))
            if endpoint:
                self._observed.add(endpoint)
                progress = True
            elif observation_kind.startswith("memory."):
                progress = True
            self._emit(
                "loop.observation.ingested",
                iteration=self._iterations,
                payload={"observation": observation},
            )
        for fact in result.facts:
            self._facts.append(fact)
            self._observed.add(fact.subject)
            progress = True
        if result.evidence_refs:
            before = len(self._evidence_refs)
            self._evidence_refs.update(result.evidence_refs)
            progress = progress or len(self._evidence_refs) > before
        if progress:
            self._last_progress_iteration = self._iterations
        self._last_tool_observations = tuple(result.observations)
        self._emit(
            "loop.state.patched",
            iteration=self._iterations,
            payload={"coverage_ratio": self.coverage.ratio},
        )
        self._checkpoint()
        return False

    def _handle_proposal(
        self,
        proposal: ActionProposal,
    ) -> tuple[LoopToolResult | None, bool]:
        mode, synthetic = self._prepare_proposal(proposal)
        if mode == "stop":
            self._status = "inconclusive"
            self._stop_reason = "model_looping"
            self._emit(
                "loop.iteration.ended",
                iteration=self._iterations,
                payload={"status": self._status},
            )
            return None, True
        if mode == "skip":
            should_stop = self._aggregate_result(proposal, synthetic)
            return synthetic, should_stop

        result = self._execute_action(proposal)
        if result is None or result.status == "denied":
            return result, False
        if result.status == "finished":
            return result, True
        should_stop = self._aggregate_result(proposal, result)
        return result, should_stop

    def _run_proposals_parallel(
        self,
        proposals: tuple[ActionProposal, ...],
    ) -> bool:
        prepared: list[tuple[ActionProposal, str, LoopToolResult | None]] = []
        for proposal in proposals:
            mode, synthetic = self._prepare_proposal(proposal)
            if mode == "stop":
                self._status = "inconclusive"
                self._stop_reason = "model_looping"
                self._emit(
                    "loop.iteration.ended",
                    iteration=self._iterations,
                    payload={"status": self._status},
                )
                return True
            prepared.append((proposal, mode, synthetic))
        execute = [
            item for item in prepared if item[1] == "execute"
        ]
        skip = [
            item for item in prepared if item[1] == "skip"
        ]
        with ThreadPoolExecutor(
            max_workers=min(4, max(1, len(execute)))
        ) as pool:
            future_by_proposal = {
                id(proposal): pool.submit(
                    self._tools.execute,
                    proposal,
                    idempotency_key=(
                        f"{self._spec.loop_id}:{self._iterations}:"
                        f"{proposal.action_id}:0"
                    ),
                )
                for proposal, _, _ in execute
            }
            for proposal, mode, synthetic in prepared:
                if mode == "skip":
                    result = synthetic
                else:
                    result = future_by_proposal[id(proposal)].result()
                if result is None or result.status == "denied":
                    self._denied += 1
                    self._emit(
                        "loop.action.denied",
                        iteration=self._iterations,
                    )
                    continue
                if result.status == "failed":
                    self._tool_errors += 1
                    result = self._attach_failure_feedback(
                        proposal,
                        result,
                        result.error_category or "tool_invalid",
                    )
                if result.status == "completed":
                    self._tool_calls += 1
                    self._token_estimate += self._estimate_tokens(
                        proposal,
                        result,
                    )
                if result.status == "finished":
                    self._evaluate_oracle()
                    if self._status == "running":
                        self._status = "inconclusive"
                        self._stop_reason = "oracle_not_verified"
                    self._emit(
                        "loop.iteration.ended",
                        iteration=self._iterations,
                        payload={"status": self._status},
                    )
                    return True
                if self._aggregate_result(proposal, result):
                    return True
        return False

    def _execute_action(self, proposal: ActionProposal) -> LoopToolResult | None:
        attempts = 0
        while True:
            result = self._tools.execute(
                proposal,
                idempotency_key=(
                    f"{self._spec.loop_id}:{self._iterations}:"
                    f"{proposal.action_id}:{attempts}"
                ),
            )
            if result.status == "denied":
                self._denied += 1
                self._emit("loop.action.denied", iteration=self._iterations)
                return None
            if result.status == "side_effect_unknown":
                self._status = "failed"
                self._stop_reason = "side_effect_unknown"
                self._tool_errors += 1
                self._emit("side_effect_unknown", iteration=self._iterations)
                return result
            if result.status == "completed":
                self._tool_calls += 1
                self._token_estimate += self._estimate_tokens(proposal, result)
                return result
            self._tool_errors += 1
            category = result.error_category or "tool_invalid"
            if category == "transient" and attempts < self._max_retries:
                self._retries += 1
                self._emit(
                    "loop.retry",
                    iteration=self._iterations,
                    payload={"attempt": attempts + 1, "category": category},
                )
                attempts += 1
                continue
            if category == "environment_unavailable":
                self._status = "waiting"
                self._stop_reason = "environment_unavailable"
                self._emit(
                    "loop.waiting",
                    iteration=self._iterations,
                    payload={"reason": category},
                )
                return result
            if category == "oracle_failed":
                self._status = "inconclusive"
                self._stop_reason = "oracle_failed"
                self._emit(
                    "loop.inconclusive",
                    iteration=self._iterations,
                    payload={"reason": category},
                )
                return result
            if category == "policy_denied":
                self._denied += 1
                self._emit("loop.action.denied", iteration=self._iterations)
                return None
            result = self._attach_failure_feedback(
                proposal,
                result,
                category,
            )
            return result

    def _attach_failure_feedback(
        self,
        proposal: ActionProposal,
        result: LoopToolResult,
        category: str,
    ) -> LoopToolResult:
        self._tool_failures[proposal.tool_ref] = (
            self._tool_failures.get(proposal.tool_ref, 0) + 1
        )
        feedback = {
            "tool": proposal.tool_ref,
            "status": "failed",
            "stdout": "",
            "stderr": result.error,
            "error_category": category,
            "guidance": _failure_guidance(category),
            "reasoning_content": proposal.reasoning or "",
        }
        feedback_result = replace(
            result,
            observations=(*result.observations, feedback),
        )
        self._last_tool_observations = (feedback,)
        attempts = self._tool_failures[proposal.tool_ref]
        if attempts >= self._tool_failure_replan_threshold:
            self._replans += 1
            self._emit(
                "loop.replan.suggested",
                iteration=self._iterations,
                payload={
                    "reason": "tool_repeated_failure",
                    "tool": proposal.tool_ref,
                    "attempts": attempts,
                    "error_category": category,
                },
            )
        return feedback_result

    @staticmethod
    def _estimate_tokens(
        proposal: ActionProposal,
        result: LoopToolResult,
    ) -> int:
        input_chars = len(json.dumps(proposal.input, sort_keys=True))
        output_chars = sum(
            len(json.dumps(observation, ensure_ascii=True, default=str))
            for observation in result.observations
        )
        return (input_chars + output_chars) // 4

    def _finish_metrics(self) -> LoopMetrics:
        attempted = [
            event.payload.get("tool")
            for event in self._events
            if event.event_type == "loop.action.proposed"
        ]
        allowed = set(self._spec.allowed_tools)
        selected = set(attempted)
        accuracy = (len(selected & allowed) / len(selected)) if selected else 1.0
        return LoopMetrics(
            iterations=self._iterations,
            tool_calls=self._tool_calls,
            tool_errors=self._tool_errors,
            denied=self._denied,
            retries=self._retries,
            replan_count=self._replans,
            duplicate_actions=self._duplicate_actions,
            evidence_count=len(self._evidence_refs),
            token_estimate=self._token_estimate,
            completion=self._status in ("succeeded", "failed"),
            success=self._status == "succeeded",
            verified_result=(
                self._oracle_result is not None
                and self._oracle_result.status == "verified"
            ),
            stop_accuracy=(
                1.0
                if self._stop_reason in ("oracle_verified", "coverage_met")
                else 0.0
            ),
            tool_selection_accuracy=round(accuracy, 3),
            duplicate_action_rate=round(
                self._duplicate_actions / max(1, self._tool_calls),
                3,
            ),
            progress_ratio=round(
                self._last_progress_iteration / max(1, self._iterations),
                3,
            ),
        )

    def _evaluate_oracle(self) -> None:
        result = self._oracle.evaluate(self._state(), tuple(self._facts), self.coverage)
        self._oracle_result = result
        self._emit(
            "loop.oracle.evaluated",
            iteration=self._iterations,
            payload={
                "status": result.status,
                "reason": result.reason,
                "metadata": result.metadata,
            },
        )
        if result.status == "verified":
            self._status = "succeeded"
            self._stop_reason = "oracle_verified"
            self._emit("loop.succeeded", iteration=self._iterations)
            return
        if (
            self._status == "running"
            and self._spec.stop_on_coverage
            and self.coverage.known
            and self.coverage.ratio >= self._spec.stop_on_coverage
        ):
            self._status = "succeeded"
            self._stop_reason = "coverage_met"
        self._emit("loop.succeeded", iteration=self._iterations)


def _loop_event_to_dict(event: LoopEvent) -> dict[str, Any]:
    return {
        "loop_id": event.loop_id,
        "event_type": event.event_type,
        "sequence": event.sequence,
        "iteration": event.iteration,
        "payload": dict(event.payload),
    }


def _loop_event_from_dict(data: dict[str, Any]) -> LoopEvent:
    return LoopEvent(
        loop_id=str(data["loop_id"]),
        event_type=str(data["event_type"]),
        sequence=int(data["sequence"]),
        iteration=(
            int(data["iteration"])
            if data.get("iteration") is not None
            else None
        ),
        payload=dict(data.get("payload") or {}),
    )


def _fact_to_dict(fact: FactRecord) -> dict[str, Any]:
    return dict(fact.__dict__)


def _fact_from_dict(data: dict[str, Any]) -> FactRecord:
    return FactRecord(**data)


def _oracle_to_dict(oracle: OracleResult | None) -> dict[str, Any] | None:
    if oracle is None:
        return None
    return {
        "status": oracle.status,
        "evidence_refs": list(oracle.evidence_refs),
        "reason": oracle.reason,
        "metadata": dict(oracle.metadata),
    }


def _oracle_from_dict(data: dict[str, Any] | None) -> OracleResult | None:
    if data is None:
        return None
    return OracleResult(
        status=str(data["status"]),
        evidence_refs=tuple(data.get("evidence_refs", ())),
        reason=str(data.get("reason", "")),
        metadata=dict(data.get("metadata") or {}),
    )


def _failure_guidance(category: str) -> str:
    if category == "tool_invalid":
        return (
            "Fix the arguments using the tool schema; do not repeat the "
            "same call unchanged."
        )
    if category == "policy_denied":
        return "Choose an allowed tool or reduce scope; do not retry."
    if category == "environment_unavailable":
        return "Wait for the environment and continue when available."
    if category == "oracle_failed":
        return "Collect stronger evidence or choose a different hypothesis."
    if category == "transient":
        return "Retry with backoff; the failure may resolve."
    return (
        "Inspect the error and choose a different action or adjust "
        "arguments; do not repeat the identical call."
    )
