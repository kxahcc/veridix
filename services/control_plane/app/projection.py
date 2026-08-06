from __future__ import annotations

from .contracts import AgentEvent


def project_run(events: list[AgentEvent]) -> dict:
    status = "queued"
    observations: list[dict] = []
    tool_calls: list[dict] = []
    stop_reason: str | None = None

    for event in events:
        if event.event_type == "run.queued":
            status = "queued"
        elif event.event_type in ("run.claimed", "run.started", "run.resumed"):
            status = "running"
        elif event.event_type == "run.paused":
            status = "paused"
        elif event.event_type == "run.resumed":
            status = "running"
        elif event.event_type == "run.succeeded":
            status = "succeeded"
            stop_reason = event.payload.get("stop_reason")
        elif event.event_type == "run.failed":
            status = "failed"
        elif event.event_type == "run.cancelled":
            status = "cancelled"
        elif event.event_type == "side_effect_unknown":
            status = "attention_required"
        elif (
            event.event_type == "resource.recovered"
            and event.payload.get("reobserve_required") is True
        ):
            status = "attention_required"
        elif event.event_type == "tool.completed":
            tool_calls.append(
                {
                    "action_id": event.payload.get("action_id"),
                    "tool": event.payload.get("tool"),
                }
            )
        elif event.event_type == "observation.ingested":
            observations.append(event.payload)

    return {
        "status": status,
        "observations": observations,
        "tool_calls": tool_calls,
        "stop_reason": stop_reason,
        "event_count": len(events),
    }
