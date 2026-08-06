from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    LoopSpec,
    NodeSpec,
)
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
from services.control_plane.app.contracts import AgentEvent
from services.mission_orchestrator.blackboard import Blackboard
from services.mission_orchestrator.scheduler import GraphScheduler

from .benchmark import BenchmarkRunner
from .models import BenchmarkResult, Scenario


def single_runner(scenario: Scenario):
    spec = LoopSpec(
        loop_id="loop_discovery",
        profile="web_discovery",
        max_iterations=4,
        allowed_tools=("proxy.list",),
        budget={"known_endpoints": ("/", "/admin", "/api/health")},
    )
    model = ScriptedLoopModel(
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
    )
    runner = LoopRunner(
        spec,
        model,
        WebDiscoveryTool(("/", "/admin", "/api/health")),
        WebDiscoveryOracle(),
    )
    result = runner.run(known_endpoints=("/", "/admin", "/api/health"))
    return [
        AgentEvent(
            event_id="run.started",
            event_type="run.started",
            stream_id=scenario.scenario_id,
            run_id=scenario.scenario_id,
            actor="benchmark",
            sequence=1,
        ),
        AgentEvent(
            event_id="finding.verified",
            event_type="finding.verified",
            stream_id=scenario.scenario_id,
            run_id=scenario.scenario_id,
            actor="benchmark",
            sequence=2,
            payload={"finding_id": "finding_golden"} if result.status == "succeeded" else {},
        ),
        AgentEvent(
            event_id="run.succeeded",
            event_type="run.succeeded",
            stream_id=scenario.scenario_id,
            run_id=scenario.scenario_id,
            actor="benchmark",
            sequence=3,
        ),
    ]


def graph_runner(scenario: Scenario):
    blackboard = Blackboard(scenario.scenario_id)
    scheduler = GraphScheduler(
        graph_id=scenario.scenario_id,
        mission_ref=scenario.scenario_id,
        nodes={
            "discovery": NodeSpec(
                node_id="discovery",
                node_type="loop",
                loop_spec=LoopSpec(
                    loop_id="loop_discovery",
                    profile="web_discovery",
                    max_iterations=4,
                    allowed_tools=("proxy.list",),
                    budget={"known_endpoints": ("/", "/admin", "/api/health")},
                ),
            ),
            "verifier": NodeSpec(
                node_id="verifier",
                node_type="loop",
                loop_spec=LoopSpec(
                    loop_id="loop_verifier",
                    profile="verifier",
                    max_iterations=4,
                    allowed_tools=("evidence.replay",),
                    budget={"hypotheses": ("/admin",)},
                ),
                preconditions=("/admin",),
            ),
        },
        edges={"discovery": ("verifier",)},
        blackboard=blackboard,
        runner_factory=_graph_factory(),
        target_ref=scenario.target_ref,
    )
    scheduler.execute_node("discovery")
    scheduler.execute_node("verifier")

    events = [
        AgentEvent(
            event_id="run.started",
            event_type="run.started",
            stream_id=scenario.scenario_id,
            run_id=scenario.scenario_id,
            actor="benchmark",
            sequence=1,
        )
    ]
    sequence = 2
    for node_id, state in scheduler.state.node_states.items():
        if state.status == "succeeded":
            events.append(
                AgentEvent(
                    event_id=f"finding.verified.{node_id}",
                    event_type="finding.verified",
                    stream_id=scenario.scenario_id,
                    run_id=scenario.scenario_id,
                    actor="benchmark",
                    sequence=sequence,
                    payload={"finding_id": f"finding_{node_id}"},
                )
            )
            sequence += 1
    events.append(
        AgentEvent(
            event_id="run.succeeded",
            event_type="run.succeeded",
            stream_id=scenario.scenario_id,
            run_id=scenario.scenario_id,
            actor="benchmark",
            sequence=sequence,
        )
    )
    return events


def compare_single_vs_graph(
    scenario: Scenario,
    *,
    runs: int = 3,
) -> tuple[BenchmarkResult, BenchmarkResult, dict, str]:
    single = BenchmarkRunner(single_runner).run(scenario, runs=runs)
    graph = BenchmarkRunner(graph_runner).run(
        Scenario(
            scenario_id=scenario.scenario_id,
            name=scenario.name,
            target_ref=scenario.target_ref,
            mode="graph",
        ),
        runs=runs,
    )
    delta = BenchmarkRunner.compare(single, graph)
    verified_delta = delta["verified_avg"]["delta"]
    cost_delta = delta["cost_avg"]["delta"]
    duplicate_delta = delta["duplicate_actions_avg"]["delta"]
    recommendation = (
        "graph"
        if verified_delta >= 0 and cost_delta <= 0 and duplicate_delta <= 0
        else "single"
    )
    return single, graph, delta, recommendation


def _graph_factory():
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
        raise AssertionError(spec.profile)

    return factory
