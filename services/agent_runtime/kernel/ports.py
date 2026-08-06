from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from services.control_plane.app.contracts import AgentEvent

from .contracts import (
    ActionProposal,
    AgentRunSpec,
    Checkpoint,
    ContextView,
    ExecutionRequest,
    ExecutionOutcome,
    LoopResult,
    LoopSpec,
    LoopState,
    LoopToolResult,
    ModelDecision,
    ModelEvent,
    OracleResult,
    PolicyDecision,
    ToolCall,
)


class EventSinkPort(ABC):
    @abstractmethod
    def emit(
        self,
        *,
        stream_id: str,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> AgentEvent:
        raise NotImplementedError

    @abstractmethod
    def replay(self, stream_id: str) -> list[AgentEvent]:
        raise NotImplementedError

    @abstractmethod
    def latest_sequence(self, stream_id: str) -> int:
        raise NotImplementedError


class TurnBackendPort(ABC):
    @abstractmethod
    def stream(self, context: ContextView) -> Iterable[ModelEvent]:
        raise NotImplementedError


class ToolBrokerPort(ABC):
    @abstractmethod
    def authorize(self, call: ToolCall, spec: AgentRunSpec) -> PolicyDecision:
        raise NotImplementedError

    @abstractmethod
    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        raise NotImplementedError


class CheckpointStorePort(ABC):
    @abstractmethod
    def save(self, checkpoint: Checkpoint) -> None:
        raise NotImplementedError

    @abstractmethod
    def load(self, run_id: str) -> Checkpoint | None:
        raise NotImplementedError


class LoopModelPort(ABC):
    @abstractmethod
    def propose(self, state: LoopState, context: dict[str, Any]) -> ModelDecision:
        raise NotImplementedError


class LoopToolPort(ABC):
    @abstractmethod
    def execute(
        self,
        proposal: ActionProposal,
        *,
        idempotency_key: str,
    ) -> LoopToolResult:
        raise NotImplementedError


class OraclePort(ABC):
    @abstractmethod
    def evaluate(
        self,
        state: LoopState,
        facts: tuple,
        coverage,
    ) -> OracleResult:
        raise NotImplementedError
