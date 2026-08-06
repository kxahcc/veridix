from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Callable

from .contracts import (
    ActionProposal,
    AgentRunSpec,
    ContextBlocks,
    ContextView,
    ExecutionRequest,
    FactRecord,
    LoopState,
    LoopToolResult,
    ModelDecision,
    ToolCall,
)
from .context_trimming import trim_observations
from .fault_injector import FaultInjector
from .ports import LoopModelPort, LoopToolPort, ToolBrokerPort, TurnBackendPort


class TurnLoopModelAdapter(LoopModelPort):
    """Bridges the kernel turn backend to a LoopRunner model port."""

    def __init__(
        self,
        backend: TurnBackendPort,
        *,
        target_ref: str,
        mission: str,
        max_turns: int = 5,
        max_context_tokens: int = 32_000,
        keep_recent_observations: int = 2,
        summarizer: (
            Callable[[tuple[dict[str, Any], ...]], str] | None
        ) = None,
        context_blocks_provider: Callable[[], ContextBlocks] | None = None,
    ) -> None:
        self._backend = backend
        self._target_ref = target_ref
        self._mission = mission
        self._max_turns = max_turns
        self._max_context_tokens = max_context_tokens
        self._keep_recent_observations = keep_recent_observations
        self._summarizer = summarizer
        self._context_blocks_provider = context_blocks_provider
        self._observation_history: list[dict[str, Any]] = []

    def propose(self, state: LoopState, context: dict[str, Any]) -> ModelDecision:
        if state.observation_history:
            self._observation_history = [
                dict(observation)
                for observation in state.observation_history
            ]
        else:
            for observation in state.last_tool_observations:
                self._observation_history.append(dict(observation))
        observations, blocks = self._trimmed_observations()
        view = ContextView(
            mission=self._mission,
            target_ref=self._target_ref,
            observations=observations,
            remaining_budget=max(0, self._max_turns - state.iteration),
            context_blocks=blocks,
        )
        proposals: list[ActionProposal] = []
        finish_text = ""
        for event in self._backend.stream(view):
            if event.tool_call is not None:
                proposals.append(
                    ActionProposal(
                        action_id=(
                            f"loop_{state.loop_id}_"
                            f"{state.iteration}_{len(proposals)}_"
                            f"{event.tool_call.id}"
                        ),
                        tool_ref=event.tool_call.name,
                        input=event.tool_call.arguments,
                        reasoning=event.reasoning_content or "",
                    )
                )
            elif event.type == "model.finish":
                finish_text = event.text or ""
        if proposals:
            return ModelDecision(
                kind="action",
                actions=tuple(proposals),
                reasoning=finish_text,
            )
        return ModelDecision(kind="finish", reasoning=finish_text or "no_tool_decision")

    def _trimmed_observations(
        self,
    ) -> tuple[tuple[dict[str, Any], ...], ContextBlocks]:
        max_context_tokens = self._effective_max_context_tokens()
        observations, heuristic, removed = trim_observations(
            tuple(self._observation_history),
            max_context_tokens=max_context_tokens,
            keep_recent=self._keep_recent_observations,
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
            self._context_blocks_provider()
            if self._context_blocks_provider is not None
            else ContextBlocks()
        )
        if summary:
            blocks = replace(
                blocks,
                summaries=(*blocks.summaries, summary),
            )
        return observations, blocks

    def _effective_max_context_tokens(self) -> int:
        usage = getattr(self._backend, "last_usage", None)
        if not isinstance(usage, dict):
            return self._max_context_tokens
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        if prompt_tokens >= self._max_context_tokens - 4096:
            return max(4096, self._max_context_tokens - 8192)
        return self._max_context_tokens


class BrokerLoopTool(LoopToolPort):
    """Bridges ToolBroker execution to a LoopRunner tool port."""

    def __init__(
        self,
        broker: ToolBrokerPort,
        spec: AgentRunSpec,
        tool_args: dict[str, dict] | None = None,
        forced_tool_args: dict[str, dict] | None = None,
        argument_validator: Callable[[str, dict[str, Any]], list[str]] | None = None,
        fault_injector: FaultInjector | None = None,
        node_allowed_tools: tuple[str, ...] | None = None,
    ) -> None:
        self._broker = broker
        self._spec = spec
        self._tool_args = tool_args or {}
        self._forced_tool_args = forced_tool_args or {}
        self._argument_validator = argument_validator
        self._fault_injector = fault_injector
        self._node_allowed_tools = node_allowed_tools

    def execute(
        self,
        proposal: ActionProposal,
        *,
        idempotency_key: str,
    ) -> LoopToolResult:
        if proposal.tool_ref == "run.finish":
            return LoopToolResult(
                status="finished",
                observations=(),
                facts=(),
                evidence_refs=(),
            )
        call = ToolCall(
            id=f"loop_{proposal.action_id}",
            name=proposal.tool_ref,
            arguments={
                **self._tool_args.get(proposal.tool_ref, {}),
                **proposal.input,
                **self._forced_tool_args.get(proposal.tool_ref, {}),
            },
        )
        if not call.arguments.get("target") and not call.arguments.get("url"):
            call.arguments["target"] = self._spec.target_ref
            call.arguments["url"] = self._spec.target_ref
        if (
            self._node_allowed_tools is not None
            and proposal.tool_ref not in self._node_allowed_tools
        ):
            return LoopToolResult(
                status="denied",
                error=(
                    f"tool_not_in_node_scope: {proposal.tool_ref} "
                    f"not in {','.join(self._node_allowed_tools)}"
                ),
            )
        if self._fault_injector is not None:
            injected = self._fault_injector.maybe_fail(
                proposal.tool_ref,
                call.arguments,
            )
            if injected is not None:
                return injected
        if self._argument_validator is not None:
            validation_errors = self._argument_validator(
                proposal.tool_ref,
                call.arguments,
            )
            if validation_errors:
                return LoopToolResult(
                    status="failed",
                    error="tool_invalid: " + "; ".join(validation_errors),
                    error_category="tool_invalid",
                    retryable=True,
                )
        decision = self._broker.authorize(call, self._spec)
        if not decision.allowed:
            return LoopToolResult(
                status="denied",
                error=decision.explanation or decision.rule,
            )
        outcome = self._broker.execute(
            ExecutionRequest(
                action_id=proposal.action_id,
                run_id=self._spec.run_id,
                tool_ref=proposal.tool_ref,
                input=call.arguments,
                idempotency_key=idempotency_key,
            )
        )
        result = outcome.result
        if result.status != "completed":
            return LoopToolResult(
                status="failed",
                error=result.stderr or f"exit_code={result.exit_code}",
                error_category="tool_invalid",
                retryable=outcome.replayed,
            )
        subject = str(
            call.arguments.get("target")
            or call.arguments.get("url")
            or proposal.tool_ref
        )
        value = result.stdout[:800] or "completed"
        fact = FactRecord(
            fact_id=f"fact_loop_{proposal.action_id}",
            subject=subject,
            predicate=f"observed:{proposal.tool_ref}",
            value=value,
            source_refs=result.artifact_refs,
            confidence=0.7,
            trust="project_observed",
        )
        is_memory_tool = proposal.tool_ref.startswith("memory.")
        raw_observations = result.observations or (
            {
                "tool": proposal.tool_ref,
                "stdout": result.stdout[:2000],
            },
        )

        def _attach_reasoning(observation: dict[str, Any]) -> dict[str, Any]:
            item = dict(_with_stdout(observation))
            if proposal.reasoning:
                item["reasoning_content"] = proposal.reasoning
            return item

        observations = tuple(
            _attach_reasoning(observation)
            for observation in raw_observations
        )
        facts = [] if is_memory_tool else [fact]
        for index, observation in enumerate(observations):
            category = (
                observation.get("vuln_category")
                or observation.get("template_id")
                or observation.get("rule_id")
                or observation.get("vulnerability_id")
            )
            if isinstance(observation, dict) and category:
                facts.append(
                    FactRecord(
                        fact_id=(
                            f"fact_finding_{proposal.action_id}_{index}"
                        ),
                        subject=str(
                            observation.get("endpoint")
                            or observation.get("url")
                            or call.arguments.get("target")
                            or call.arguments.get("url")
                            or proposal.tool_ref
                        ),
                        predicate="finding",
                        value=str(category),
                        source_refs=result.artifact_refs,
                        confidence=0.8,
                        trust="project_observed",
                        metadata=dict(observation),
                    )
                )
            if (
                isinstance(observation, dict)
                and observation.get("replay_proof")
            ):
                facts.append(
                    FactRecord(
                        fact_id=f"fact_replay_{proposal.action_id}",
                        subject=str(
                            observation.get("endpoint")
                            or observation.get("url")
                            or call.arguments.get("request_id")
                            or proposal.tool_ref
                        ),
                        predicate="replay_proof",
                        value="verified",
                        source_refs=(
                            result.artifact_refs
                            or (str(observation.get("request_id")),)
                        ),
                        confidence=0.9,
                        trust="project_observed",
                    )
                )
            if (
                isinstance(observation, dict)
                and observation.get("kind") == "oast_callback"
            ):
                token = str(observation.get("token") or "")
                subject = str(
                    observation.get("callback_id") or token
                )
                facts.append(
                    FactRecord(
                        fact_id=f"fact_callback_{token}",
                        subject=subject,
                        predicate="callback_evidence",
                        value="verified",
                        source_refs=result.artifact_refs,
                        confidence=0.95,
                        trust="project_observed",
                        metadata=dict(observation),
                    )
                )
                facts.append(
                    FactRecord(
                        fact_id=f"fact_finding_ssrf_{token}",
                        subject=subject,
                        predicate="finding",
                        value="SSRF",
                        source_refs=result.artifact_refs,
                        confidence=0.9,
                        trust="project_observed",
                        metadata={
                            **dict(observation),
                            "replay_proof": {
                                "callback_id": observation.get(
                                    "callback_id"
                                ),
                                "token": token,
                            },
                        },
                    )
                )
        return LoopToolResult(
            status="completed",
            observations=tuple(observations),
            facts=tuple(facts),
            evidence_refs=result.artifact_refs,
        )


def _with_stdout(observation: Any) -> Any:
    if isinstance(observation, dict) and "stdout" not in observation:
        return {
            **observation,
            "stdout": json.dumps(
                observation,
                ensure_ascii=True,
                default=str,
            ),
        }
    return observation
