from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import uuid4

from services.agent_runtime.kernel.contracts import LoopSpec, NodeSpec
from services.agent_runtime.kernel.loop_profiles import apply_loop_profile
from services.knowledge_service.memory import ProjectMemory

from .contracts import GraphPatch, GraphState


class PlannerPort(ABC):
    @abstractmethod
    def propose(
        self,
        snapshot: GraphSnapshot,
        blackboard: ProjectMemory,
        *,
        diagnostics: dict[str, list] | None = None,
    ) -> GraphPatch | None:
        raise NotImplementedError


class ChainPlanner(PlannerPort):
    """Tries planners in order and applies the first viable patch."""

    def __init__(self, planners: tuple[PlannerPort, ...]) -> None:
        if not planners:
            raise ValueError("ChainPlanner requires at least one planner")
        self._planners = planners

    def propose(
        self,
        snapshot: GraphSnapshot,
        blackboard: ProjectMemory,
        *,
        diagnostics: dict[str, list] | None = None,
    ) -> GraphPatch | None:
        for planner in self._planners:
            patch = planner.propose(
                snapshot,
                blackboard,
                diagnostics=diagnostics,
            )
            if patch is not None:
                return patch
        return None


class CoverageReplanner(PlannerPort):
    """Adds a verifier node after successful discovery when none exists."""

    def __init__(
        self,
        *,
        verifier_node: NodeSpec,
        source_node: str = "discovery",
        author: str = "coverage_replanner",
    ) -> None:
        self._verifier_node = verifier_node
        self._source_node = source_node
        self._author = author

    def propose(
        self,
        state: GraphState,
        blackboard: ProjectMemory,
        *,
        diagnostics: dict[str, list] | None = None,
    ) -> GraphPatch | None:
        node_states = state.node_states
        source = node_states.get(self._source_node)
        if source is None or source.status != "succeeded":
            return None
        if self._verifier_node.node_id in node_states:
            return None
        if not blackboard.projection():
            return None
        return GraphPatch(
            patch_id=f"patch_{uuid4().hex[:12]}",
            parent_version=state.version,
            author=self._author,
            reason="coverage_complete_add_verifier",
            affected_nodes=(self._verifier_node.node_id,),
            operations=(
                {"op": "add_node", "node": self._verifier_node},
                {
                    "op": "add_edge",
                    "source": self._source_node,
                    "target": self._verifier_node.node_id,
                },
            ),
            policy_checked=True,
        )


class CandidateVerifierPlanner(PlannerPort):
    """Adds a verifier node when discovery produced candidate findings."""

    def __init__(
        self,
        *,
        verifier_node: NodeSpec | None = None,
        author: str = "candidate_verifier_planner",
    ) -> None:
        self._verifier_node = verifier_node
        self._author = author

    def propose(
        self,
        snapshot: GraphSnapshot,
        blackboard: ProjectMemory,
        *,
        diagnostics: dict[str, list] | None = None,
    ) -> GraphPatch | None:
        node_states = snapshot.state.node_states
        if any("verifier" in node_id for node_id in node_states):
            return None
        sources = [
            node_id
            for node_id, state in node_states.items()
            if state.status == "succeeded"
            and state.result is not None
            and state.result.candidate_findings
        ]
        if not sources:
            return None
        hypotheses = tuple(
            sorted(
                {
                    str(finding)
                    for source in sources
                    for finding in (
                        node_states[source].result.candidate_findings
                    )
                }
            )
        )
        subjects = tuple(
            sorted(
                {view.fact.subject for view in blackboard.projection()}
            )
        )
        verifier = self._verifier_node or NodeSpec(
            node_id="verifier",
            node_type="loop",
            loop_spec=apply_loop_profile(
                LoopSpec(
                    loop_id="loop_verifier",
                    profile="verifier",
                    max_iterations=5,
                    allowed_tools=("evidence.replay",),
                    budget={"hypotheses": hypotheses},
                )
            ),
            preconditions=subjects,
            oracle_ref="verifier_oracle",
            harness_profile="verifier",
            sandbox_profile="S2",
        )
        operations = [
            {"op": "add_node", "node": verifier},
            *(
                {
                    "op": "add_edge",
                    "source": source,
                    "target": verifier.node_id,
                }
                for source in sources
            ),
        ]
        return GraphPatch(
            patch_id=f"patch_{verifier.node_id}_{len(operations)}",
            parent_version=snapshot.version,
            author=self._author,
            reason="candidates_need_verification",
            affected_nodes=(verifier.node_id,),
            operations=tuple(operations),
            policy_checked=True,
        )


class FailureDrivenReplanner(PlannerPort):
    """Adds a fallback node when a role node failed and requested replan.

    Consumes ``loop.replan.suggested`` diagnostics and failed node states to
    insert an alternate scanner/verifier path before the original reporter.
    """

    def __init__(
        self,
        *,
        fallback_node: NodeSpec,
        failed_node: str,
        target_node: str | None = None,
        author: str = "failure_driven_replanner",
    ) -> None:
        self._fallback_node = fallback_node
        self._failed_node = failed_node
        self._target_node = target_node
        self._author = author

    def propose(
        self,
        snapshot: GraphSnapshot,
        blackboard: ProjectMemory,
        *,
        diagnostics: dict[str, list] | None = None,
    ) -> GraphPatch | None:
        if self._fallback_node.node_id in snapshot.state.node_states:
            return None
        failed_state = snapshot.state.node_states.get(self._failed_node)
        if failed_state is None:
            return None
        replan_requested = _has_replan_signal(diagnostics)
        failed_status = failed_state.status in (
            "inconclusive",
            "failed",
            "dead_letter",
            "backpressure_waiting",
        )
        if not replan_requested and not failed_status:
            return None
        operations: list[dict] = [
            (
                {
                    "op": "remove_edge",
                    "source": self._failed_node,
                    "target": self._target_node,
                }
                if self._target_node
                else None
            ),
            {"op": "add_node", "node": self._fallback_node},
            {
                "op": "add_edge",
                "source": self._failed_node,
                "target": self._fallback_node.node_id,
            },
        ]
        if (
            self._target_node
            and self._target_node in snapshot.state.node_states
        ):
            operations.append(
                {
                    "op": "add_edge",
                    "source": self._fallback_node.node_id,
                    "target": self._target_node,
                }
            )
        operations = [op for op in operations if op is not None]
        return GraphPatch(
            patch_id=f"patch_{self._fallback_node.node_id}_{len(operations)}",
            parent_version=snapshot.version,
            author=self._author,
            reason="failure_recovery_add_fallback",
            affected_nodes=(self._fallback_node.node_id,),
            operations=tuple(operations),
            policy_checked=True,
        )


def _has_replan_signal(diagnostics: dict[str, list] | None) -> bool:
    for events in (diagnostics or {}).values():
        for event in events:
            event_type = (
                event.get("event_type")
                if isinstance(event, dict)
                else getattr(event, "event_type", "")
            )
            if event_type == "loop.replan.suggested":
                return True
    return False
