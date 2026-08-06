from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from services.agent_runtime.kernel.contracts import LoopSpec
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loops import (
    ScriptedLoopModel,
    VerifierOracle,
    VerifierTool,
    WebDiscoveryOracle,
    WebDiscoveryTool,
    action,
    finish,
)
from services.agent_runtime.kernel.contracts import ActionProposal
from services.agent_runtime.roles import (
    AgentRole,
    RoleGraphRunner,
    webappsec_role_template,
)


@dataclass(frozen=True)
class LoopBenchmarkMetrics:
    verified: bool
    actions: int
    duplicate_actions: int
    handoffs: int
    cost_estimate: float


def single_loop_metrics(
    *,
    target_ref: str,
    runner_factory: Callable[[LoopSpec], LoopRunner],
) -> LoopBenchmarkMetrics:
    spec = LoopSpec(
        loop_id="single_baseline",
        profile="web_discovery",
        max_iterations=10,
        allowed_tools=("proxy.list",),
        budget={
            "known_endpoints": ("/", "/admin", "/api/health"),
        },
    )
    runner = runner_factory(spec)
    result = runner.run(
        known_endpoints=("/", "/admin", "/api/health"),
        hypotheses=(),
    )
    action_keys = [
        str(event.payload.get("tool"))
        for event in runner.events
        if event.event_type == "loop.action.proposed"
    ]
    return LoopBenchmarkMetrics(
        verified=result.status == "succeeded",
        actions=len(action_keys),
        duplicate_actions=len(action_keys) - len(set(action_keys)),
        handoffs=0,
        cost_estimate=float(len(action_keys)),
    )


def role_graph_metrics(
    *,
    target_ref: str,
    runner_factory: Callable[[LoopSpec], LoopRunner],
    roles: tuple[AgentRole, ...] | None = None,
) -> LoopBenchmarkMetrics:
    roles = roles or webappsec_role_template(target_ref=target_ref)
    graph = RoleGraphRunner(
        roles=roles,
        runner_factory=runner_factory,
        graph_id="role_graph_benchmark",
        mission_ref="benchmark",
        target_ref=target_ref,
    )
    result = graph.run()
    statuses = dict(result.node_statuses)
    metrics = result.metrics
    return LoopBenchmarkMetrics(
        verified=all(
            status == "succeeded"
            for status in statuses.values()
        ),
        actions=(
            metrics.handoffs
            + metrics.duplicate_actions
            + len(statuses)
        ),
        duplicate_actions=metrics.duplicate_actions,
        handoffs=metrics.handoffs,
        cost_estimate=float(metrics.handoffs + metrics.duplicate_actions + len(statuses)),
    )


def compare_single_vs_multi_role(
    *,
    target_ref: str,
    runner_factory: Callable[[LoopSpec], LoopRunner],
    roles: tuple[AgentRole, ...] | None = None,
    runs: int = 1,
) -> dict[str, Any]:
    singles = [
        single_loop_metrics(
            target_ref=target_ref,
            runner_factory=runner_factory,
        )
        for _ in range(max(1, runs))
    ]
    graphs = [
        role_graph_metrics(
            target_ref=target_ref,
            runner_factory=runner_factory,
            roles=roles,
        )
        for _ in range(max(1, runs))
    ]
    single = _average_metrics(singles)
    graph = _average_metrics(graphs)
    single_verified_runs = sum(1 for item in singles if item.verified)
    graph_verified_runs = sum(1 for item in graphs if item.verified)
    recommendation = (
        "graph"
        if graph.verified
        and (not single.verified or graph.duplicate_actions <= single.duplicate_actions)
        and graph.handoffs >= 1
        else "single"
    )
    return {
        "target_ref": target_ref,
        "runs": max(1, runs),
        "single_verified_runs": single_verified_runs,
        "graph_verified_runs": graph_verified_runs,
        "single": {
            "verified": single.verified,
            "actions": single.actions,
            "duplicate_actions": single.duplicate_actions,
            "cost_estimate": single.cost_estimate,
            "handoffs": single.handoffs,
        },
        "graph": {
            "verified": graph.verified,
            "actions": graph.actions,
            "duplicate_actions": graph.duplicate_actions,
            "handoffs": graph.handoffs,
            "cost_estimate": graph.cost_estimate,
        },
        "recommendation": recommendation,
    }


def scripted_role_runner_factory():
    """Deterministic scripted roles for single-vs-graph benchmark."""

    def factory(spec: LoopSpec) -> LoopRunner:
        if spec.profile == "web_discovery":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(
                            ActionProposal(
                                action_id="d1",
                                tool_ref="proxy.list",
                                input={"path": "/"},
                            )
                        ),
                        finish("coverage complete"),
                    ]
                ),
                WebDiscoveryTool(("/", "/admin", "/api/health")),
                WebDiscoveryOracle(),
            )
        if spec.profile == "verifier":
            return LoopRunner(
                spec,
                ScriptedLoopModel(
                    [
                        action(
                            ActionProposal(
                                action_id="v1",
                                tool_ref="evidence.replay",
                                input={"candidate": "/admin"},
                            )
                        ),
                        finish("verified"),
                    ]
                ),
                VerifierTool({"/admin": "replay://proof"}),
                VerifierOracle(),
            )
        raise AssertionError(f"unsupported benchmark profile {spec.profile}")

    return factory


def _average_metrics(
    items: list[LoopBenchmarkMetrics],
) -> LoopBenchmarkMetrics:
    if not items:
        return LoopBenchmarkMetrics(
            verified=False,
            actions=0,
            duplicate_actions=0,
            handoffs=0,
            cost_estimate=0.0,
        )
    count = len(items)
    return LoopBenchmarkMetrics(
        verified=all(item.verified for item in items),
        actions=round(sum(item.actions for item in items) / count, 2),
        duplicate_actions=round(
            sum(item.duplicate_actions for item in items) / count,
            2,
        ),
        handoffs=round(sum(item.handoffs for item in items) / count, 2),
        cost_estimate=round(
            sum(item.cost_estimate for item in items) / count,
            3,
        ),
    )
