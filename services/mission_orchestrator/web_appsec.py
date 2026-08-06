from __future__ import annotations

from services.agent_runtime.kernel.contracts import LoopSpec, NodeSpec
from services.agent_runtime.kernel.loop_profiles import apply_loop_profile

from .blackboard import Blackboard
from .scheduler import GraphScheduler


DEFAULT_KNOWN_ENDPOINTS = ("/", "/admin", "/api/health")
DEFAULT_HYPOTHESES = ("/admin",)


def build_web_appsec_graph(
    *,
    known_endpoints: tuple[str, ...] = DEFAULT_KNOWN_ENDPOINTS,
    hypotheses: tuple[str, ...] = DEFAULT_HYPOTHESES,
    max_iterations: int = 4,
) -> tuple[dict[str, NodeSpec], dict[str, tuple[str, ...]]]:
    nodes = {
        "discovery": NodeSpec(
            node_id="discovery",
            node_type="loop",
            loop_spec=apply_loop_profile(
                LoopSpec(
                    loop_id="loop_discovery",
                    profile="web_discovery",
                    max_iterations=max_iterations,
                    allowed_tools=("proxy.list",),
                    budget={"known_endpoints": known_endpoints},
                )
            ),
        ),
        "verifier": NodeSpec(
            node_id="verifier",
            node_type="loop",
            loop_spec=apply_loop_profile(
                LoopSpec(
                    loop_id="loop_verifier",
                    profile="verifier",
                    max_iterations=max_iterations,
                    allowed_tools=("evidence.replay",),
                    budget={"hypotheses": hypotheses},
                )
            ),
            preconditions=("/admin",),
        ),
    }
    edges: dict[str, tuple[str, ...]] = {"discovery": ("verifier",)}
    return nodes, edges


def build_web_appsec_scheduler(
    *,
    graph_id: str,
    mission_ref: str,
    target_ref: str,
    runner_factory,
    blackboard: Blackboard | None = None,
    known_endpoints: tuple[str, ...] = DEFAULT_KNOWN_ENDPOINTS,
    hypotheses: tuple[str, ...] = DEFAULT_HYPOTHESES,
    max_iterations: int = 4,
) -> GraphScheduler:
    nodes, edges = build_web_appsec_graph(
        known_endpoints=known_endpoints,
        hypotheses=hypotheses,
        max_iterations=max_iterations,
    )
    return GraphScheduler(
        graph_id=graph_id,
        mission_ref=mission_ref,
        nodes=nodes,
        edges=edges,
        blackboard=blackboard or Blackboard(graph_id),
        runner_factory=runner_factory,
        target_ref=target_ref,
    )
