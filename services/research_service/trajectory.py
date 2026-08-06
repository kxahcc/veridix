from __future__ import annotations

from services.control_plane.app.contracts import AgentEvent


def compute_metrics(events: list[AgentEvent]) -> dict:
    completed = False
    verified = 0
    failed = 0
    tool_errors = 0
    cancelled = 0
    retries = 0
    replans = 0
    context_events = 0
    context_tokens = 0
    tool_calls: dict[str, int] = {}
    cost_estimate = 0.0
    handoff_loss = 0
    branch_coverage = None
    dead_letters = 0
    graph_nodes = 0
    finding_events = 0
    evidence_refs = 0
    tool_names: set[str] = set()
    graph_events = 0
    for event in events:
        if event.event_type == "run.succeeded":
            completed = True
        elif event.event_type == "run.failed":
            failed += 1
        elif event.event_type == "tool.failed":
            tool_errors += 1
        elif event.event_type == "run.cancelled":
            cancelled += 1
        elif event.event_type in ("model.retry", "tool.retry"):
            retries += 1
        elif event.event_type == "finding.verified":
            verified += 1
            finding_events += 1
            evidence_refs += len(
                event.payload.get("evidence_refs") or []
            )
        elif event.event_type == "approval.decided" and event.payload.get("state") == "approved":
            verified += 1
        elif event.event_type == "tool.completed":
            tool = str(event.payload.get("tool", "unknown"))
            tool_calls[tool] = tool_calls.get(tool, 0) + 1
            tool_names.add(tool)
        elif event.event_type == "loop.succeeded":
            verified += 1
        elif event.event_type == "loop.replan.suggested":
            replans += 1
        elif event.event_type == "graph.completed":
            graph_events += 1
            graph_nodes = int(event.payload.get("node_count") or graph_nodes)
            dead_letters = int(
                event.payload.get("dead_letters") or dead_letters
            )
            handoff_loss = int(
                event.payload.get("handoff_loss") or handoff_loss
            )
            branch_coverage = event.payload.get("branch_coverage")
        elif event.event_type == "context.projection":
            context_events += 1
            context_tokens += int(
                event.payload.get("token_estimate") or 0
            )
        cost = event.payload.get("cost_estimate")
        if isinstance(cost, (int, float)):
            cost_estimate += float(cost)
    duplicate_actions = sum(
        count - 1 for count in tool_calls.values() if count > 1
    )
    verified_count = verified
    false_completion = 1.0 if completed and verified_count == 0 else 0.0
    tool_selection_accuracy = (
        round(
            len(tool_names) / max(1, len(tool_calls)),
            3,
        )
        if tool_calls
        else 1.0
    )
    return {
        "loop_completion": 1.0 if completed else 0.0,
        "loop_success_rate": 1.0 if completed and failed == 0 else 0.0,
        "verified_result_rate": round(verified_count / max(1, len(events)), 3),
        "verified_count": verified_count,
        "duplicate_actions": duplicate_actions,
        "duplicate_action_rate": round(
            duplicate_actions / max(1, sum(tool_calls.values())),
            3,
        ),
        "failure_count": failed,
        "tool_errors": tool_errors,
        "cancelled": cancelled,
        "retries": retries,
        "replan_count": replans,
        "stop_accuracy": 1.0 if completed else 0.0,
        "tool_selection_accuracy": tool_selection_accuracy,
        "context_waste": round(
            context_tokens / max(1, context_events),
            1,
        )
        if context_events
        else 0.0,
        "handoff_loss": handoff_loss,
        "branch_coverage": (
            float(branch_coverage) if branch_coverage is not None else None
        ),
        "dead_letter_rate": round(
            dead_letters / max(1, graph_nodes),
            3,
        )
        if graph_nodes
        else 0.0,
        "graph_path_efficiency": round(
            graph_events / max(1, graph_nodes),
            3,
        )
        if graph_nodes
        else 1.0,
        "evidence_completeness": round(
            evidence_refs / max(1, finding_events),
            3,
        )
        if finding_events
        else 0.0,
        "false_completion": false_completion,
        "cost_per_verified_finding": round(
            cost_estimate / verified_count,
            3,
        )
        if verified_count
        else 0.0,
        "cost_estimate": round(cost_estimate, 3),
        "event_count": len(events),
    }
