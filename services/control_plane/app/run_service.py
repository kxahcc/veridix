from __future__ import annotations

from uuid import uuid4

from .contracts import AgentEvent, CommandEnvelope, utc_now
from .control_store import ControlStore
from .domain import ApprovalRequest, RunState, new_id
from .event_store import CommandStore, EventStore
from .outbox import OutboxStore


class DomainError(RuntimeError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


AGENT_INGEST_EVENT_TYPES = frozenset(
    {
        "resource.recovered",
        "resource.lost",
        "browser.rebuilt",
        "proxy.restarted",
        "tool.failed",
        "harness.snapshot",
        "behavior.snapshot",
    }
)
AGENT_INGEST_EVENT_ACTORS = frozenset({"agent-worker", "runner"})


class RunService:
    def __init__(
        self,
        events: EventStore,
        commands: CommandStore,
        control: ControlStore,
        outbox: OutboxStore | None = None,
    ) -> None:
        self._events = events
        self._commands = commands
        self._control = control
        self._outbox = outbox

    def start_run(self, mission_id: str, idempotency_key: str) -> RunState:
        run_id = new_id("run")
        command = CommandEnvelope(
            command_id=new_id("cmd"),
            command_type="run.start",
            run_id=run_id,
            idempotency_key=idempotency_key,
            payload={"mission_id": mission_id, "run_id": run_id},
        )
        record, replayed = self._commands.submit(command)
        if replayed:
            return self._control.get_run(record.payload["run_id"])
        try:
            return self._control.create_run(mission_id, run_id=run_id)
        except KeyError as error:
            self._commands.reject(idempotency_key, "mission_not_found")
            raise DomainError(str(error), "mission_not_found") from error

    def fork_run(self, run_id: str, idempotency_key: str) -> RunState:
        forked_run_id = new_id("run")
        command = CommandEnvelope(
            command_id=new_id("cmd"),
            command_type="run.fork",
            run_id=run_id,
            idempotency_key=idempotency_key,
            payload={"run_id": run_id, "forked_run_id": forked_run_id},
        )
        record, replayed = self._commands.submit(command)
        if replayed:
            return self._control.get_run(record.payload["forked_run_id"])
        source = self._control.get_run(run_id)
        forked = self._control.create_run(
            source.mission_id,
            run_id=forked_run_id,
            source_run_id=run_id,
        )
        self._events.append(
            AgentEvent(
                event_id=f"{run_id}:run.forked:{uuid4().hex[:8]}",
                event_type="run.forked",
                stream_id=run_id,
                run_id=run_id,
                actor="api/control",
                payload={"forked_run_id": forked.run_id},
            )
        )
        if self._outbox is not None:
            self._outbox.enqueue(
                run_id,
                "run.forked",
                {"forked_run_id": forked.run_id},
            )
        return forked

    def claim(
        self,
        run_id: str,
        worker_id: str,
        idempotency_key: str,
    ) -> RunState:
        command = CommandEnvelope(
            command_id=new_id("cmd"),
            command_type="run.claim",
            run_id=run_id,
            idempotency_key=idempotency_key,
            payload={"run_id": run_id, "worker_id": worker_id},
        )
        record, replayed = self._commands.submit(command)
        if replayed:
            return self._control.get_run(run_id)
        current = self._control.get_run(run_id)
        if current.status != "queued":
            self._commands.reject(
                idempotency_key,
                f"invalid_transition:{current.status}",
            )
            raise DomainError(
                f"cannot claim run in state {current.status}",
                "invalid_transition",
            )
        payload = {"worker_id": worker_id}
        self._events.append(
            AgentEvent(
                event_id=f"{run_id}:run.claimed:{uuid4().hex[:8]}",
                event_type="run.claimed",
                stream_id=run_id,
                run_id=run_id,
                actor=worker_id,
                payload=payload,
            )
        )
        if self._outbox is not None:
            self._outbox.enqueue(run_id, "run.claimed", payload)
        return self._control.get_run(run_id)

    def finish(
        self,
        run_id: str,
        outcome: str,
        idempotency_key: str,
        *,
        stop_reason: str = "",
        summary: str = "",
    ) -> RunState:
        if outcome not in ("succeeded", "failed"):
            raise ValueError(f"invalid finish outcome: {outcome}")
        command = CommandEnvelope(
            command_id=new_id("cmd"),
            command_type="run.finish",
            run_id=run_id,
            idempotency_key=idempotency_key,
            payload={"run_id": run_id, "outcome": outcome},
        )
        record, replayed = self._commands.submit(command)
        if replayed:
            return self._control.get_run(run_id)
        current = self._control.get_run(run_id)
        if current.status not in ("running", "attention_required"):
            self._commands.reject(
                idempotency_key,
                f"invalid_transition:{current.status}",
            )
            raise DomainError(
                f"cannot finish run in state {current.status}",
                "invalid_transition",
            )
        event_type = "run.succeeded" if outcome == "succeeded" else "run.failed"
        payload = {
            "stop_reason": stop_reason
            or ("model.finish" if outcome == "succeeded" else "worker_failed"),
            "summary": summary,
        }
        self._events.append(
            AgentEvent(
                event_id=f"{run_id}:{event_type}:{uuid4().hex[:8]}",
                event_type=event_type,
                stream_id=run_id,
                run_id=run_id,
                actor="agent-worker",
                payload=payload,
            )
        )
        if self._outbox is not None:
            self._outbox.enqueue(run_id, event_type, payload)
        return self._control.get_run(run_id)

    def takeover(
        self,
        run_id: str,
        idempotency_key: str,
        *,
        taken_by: str,
        reason: str,
    ) -> RunState:
        command = CommandEnvelope(
            command_id=new_id("cmd"),
            command_type="run.takeover",
            run_id=run_id,
            idempotency_key=idempotency_key,
            payload={
                "run_id": run_id,
                "taken_by": taken_by,
                "reason": reason,
            },
        )
        record, replayed = self._commands.submit(command)
        if replayed:
            return self._control.get_run(run_id)
        current = self._control.get_run(run_id)
        if current.status not in (
            "running",
            "attention_required",
            "waiting_human",
            "paused",
        ):
            self._commands.reject(
                idempotency_key,
                f"invalid_transition:{current.status}",
            )
            raise DomainError(
                f"cannot takeover run in state {current.status}",
                "invalid_transition",
            )
        payload = {"taken_by": taken_by, "reason": reason}
        self._events.append(
            AgentEvent(
                event_id=f"{run_id}:run.taken_over:{uuid4().hex[:8]}",
                event_type="run.taken_over",
                stream_id=run_id,
                run_id=run_id,
                actor=taken_by,
                payload=payload,
            )
        )
        self._events.append(
            AgentEvent(
                event_id=f"{run_id}:run.paused:{uuid4().hex[:8]}",
                event_type="run.paused",
                stream_id=run_id,
                run_id=run_id,
                actor=taken_by,
                payload={"reason": f"takeover:{reason}"},
            )
        )
        if self._outbox is not None:
            self._outbox.enqueue(run_id, "run.taken_over", payload)
            self._outbox.enqueue(
                run_id,
                "run.paused",
                {"reason": f"takeover:{reason}"},
            )
        return self._control.get_run(run_id)

    def pause(self, run_id: str, idempotency_key: str) -> RunState:
        return self._transition(
            run_id,
            "run.pause",
            idempotency_key,
            allowed={"running", "attention_required"},
            event_type="run.paused",
        )

    def resume(self, run_id: str, idempotency_key: str) -> RunState:
        return self._transition(
            run_id,
            "run.resume",
            idempotency_key,
            allowed={"paused"},
            event_type="run.resumed",
        )

    def cancel(self, run_id: str, idempotency_key: str) -> RunState:
        return self._transition(
            run_id,
            "run.cancel",
            idempotency_key,
            allowed={"running", "paused", "attention_required"},
            event_type="run.cancelled",
            reason="user_requested",
        )

    def request_approval(
        self,
        run_id: str,
        tool_ref: str,
        risk_level: str,
        idempotency_key: str,
        *,
        reason: str = "",
    ) -> ApprovalRequest:
        approval_id = new_id("approval")
        command = CommandEnvelope(
            command_id=new_id("cmd"),
            command_type="approval.request",
            run_id=run_id,
            idempotency_key=idempotency_key,
            payload={"run_id": run_id, "tool_ref": tool_ref, "approval_id": approval_id},
        )
        record, replayed = self._commands.submit(command)
        if replayed:
            return self._control.get_approval(record.payload["approval_id"])
        approval = self._control.request_approval(
            run_id,
            tool_ref,
            risk_level,
            policy_rule="risk_gate",
            reason=reason,
            approval_id=approval_id,
        )
        self._events.append(
            AgentEvent(
                event_id=f"{run_id}:approval.requested:{uuid4().hex[:8]}",
                event_type="approval.requested",
                stream_id=run_id,
                run_id=run_id,
                actor="api/control",
                occurred_at=utc_now(),
                payload={
                    "approval_id": approval.approval_id,
                    "tool_ref": tool_ref,
                    "risk_level": risk_level,
                    "reason": reason,
                },
            )
        )
        return approval

    def decide_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        decided_by: str,
        reason: str = "",
    ) -> ApprovalRequest:
        approval = self._control.decide_approval(
            approval_id,
            approved=approved,
            decided_by=decided_by,
            reason=reason,
        )
        self._events.append(
            AgentEvent(
                event_id=f"{approval.run_id}:approval.decided:{uuid4().hex[:8]}",
                event_type="approval.decided",
                stream_id=approval.run_id,
                run_id=approval.run_id,
                actor=decided_by,
                occurred_at=utc_now(),
                payload={
                    "approval_id": approval_id,
                    "state": approval.state,
                    "reason": reason,
                },
            )
        )
        return approval

    def ingest_resource_event(
        self,
        run_id: str,
        *,
        event_id: str,
        event_type: str,
        actor: str,
        payload: dict,
    ) -> AgentEvent:
        if actor != "agent-worker" and event_type not in AGENT_INGEST_EVENT_TYPES:
            raise DomainError(f"event type {event_type} is not allowed", "event_not_allowed")
        if actor not in AGENT_INGEST_EVENT_ACTORS:
            raise DomainError(f"actor {actor} is not allowed", "event_not_allowed")
        try:
            self._control.get_run(run_id)
        except KeyError as error:
            raise DomainError(str(error), "run_not_found") from error
        return self._events.append(
            AgentEvent(
                event_id=event_id,
                event_type=event_type,
                stream_id=run_id,
                run_id=run_id,
                actor=actor,
                payload=payload,
            )
        )

    def _transition(
        self,
        run_id: str,
        command_type: str,
        idempotency_key: str,
        *,
        allowed: set[str],
        event_type: str,
        reason: str | None = None,
    ) -> RunState:
        command = CommandEnvelope(
            command_id=new_id("cmd"),
            command_type=command_type,
            run_id=run_id,
            idempotency_key=idempotency_key,
            payload={"run_id": run_id, "reason": reason},
        )
        record, replayed = self._commands.submit(command)
        if replayed:
            return self._control.get_run(run_id)
        current = self._control.get_run(run_id)
        if current.status not in allowed:
            self._commands.reject(idempotency_key, f"invalid_transition:{current.status}")
            raise DomainError(
                f"cannot {command_type} run in state {current.status}",
                "invalid_transition",
            )
        self._events.append(
            AgentEvent(
                event_id=f"{run_id}:{event_type}:{uuid4().hex[:8]}",
                event_type=event_type,
                stream_id=run_id,
                run_id=run_id,
                actor="api/control",
                occurred_at=utc_now(),
                payload={"reason": reason or "user_requested"},
            )
        )
        if self._outbox is not None:
            self._outbox.enqueue(run_id, event_type, {"reason": reason or "user_requested"})
        return self._control.get_run(run_id)
