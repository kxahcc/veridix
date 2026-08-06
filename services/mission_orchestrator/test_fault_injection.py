from __future__ import annotations

from pathlib import Path

import pytest

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
from services.mission_orchestrator.blackboard import Blackboard
from services.mission_orchestrator.graph_store import GraphStore
from services.mission_orchestrator.scheduler import GraphScheduler


TARGET = "https://lab.example.test"


def _nodes() -> dict[str, NodeSpec]:
    return {
        "discovery": NodeSpec(
            node_id="discovery",
            node_type="loop",
            loop_spec=LoopSpec(
                loop_id="loop_discovery",
                profile="web_discovery",
                max_iterations=2,
                allowed_tools=("proxy.list",),
                budget={"known_endpoints": ("/", "/admin")},
            ),
        ),
        "verifier": NodeSpec(
            node_id="verifier",
            node_type="loop",
            loop_spec=LoopSpec(
                loop_id="loop_verifier",
                profile="verifier",
                max_iterations=2,
                allowed_tools=("evidence.replay",),
                budget={"hypotheses": ("/admin",)},
            ),
            preconditions=("/admin",),
        ),
    }


def _factory():
    def factory(spec: LoopSpec) -> LoopRunner:
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
            WebDiscoveryTool(("/", "/admin")),
            WebDiscoveryOracle(),
        )

    return factory


def _scheduler(
    db: Path | str,
    *,
    nodes=None,
    edges=None,
) -> GraphScheduler:
    return GraphScheduler(
        graph_id="graph_fault",
        mission_ref="mission_fault",
        nodes=nodes if nodes is not None else _nodes(),
        edges=edges if edges is not None else {"discovery": ("verifier",)},
        blackboard=Blackboard("graph_fault"),
        runner_factory=_factory(),
        target_ref=TARGET,
        store=GraphStore(db),
    )


def test_snapshot_rolls_back_when_persist_crashes(tmp_path: Path) -> None:
    db = tmp_path / "graphs.db"
    first = _scheduler(db)
    first.execute_node("discovery")

    second = _scheduler(db)
    store = second._store
    original_execute = store._execute

    def crashing_execute(sql: str, params=()):
        if (
            "INSERT INTO mission_graph_node_states" in sql
            and "verifier" in str(params)
        ):
            raise RuntimeError("simulated crash during snapshot")
        return original_execute(sql, params)

    store._execute = crashing_execute
    with pytest.raises(RuntimeError, match="simulated crash"):
        second.execute_node("verifier")

    restored = GraphStore(db).load("graph_fault")
    assert restored is not None
    assert restored["node_states"]["discovery"].status == "succeeded"
    assert restored["node_states"]["verifier"].status == "pending"
    assert tuple(restored["handoffs"]) == first.handoffs


def test_worker_crash_then_recovery_continues_pipeline(tmp_path: Path) -> None:
    db = tmp_path / "graphs.db"
    crashed_worker = _scheduler(db)
    crashed_worker.execute_node("discovery")

    recovered = _scheduler(db)
    recovered.execute_node("verifier")

    assert recovered.state.node_states["discovery"].status == "succeeded"
    assert recovered.state.node_states["verifier"].status == "succeeded"
    assert recovered.handoffs
    assert recovered.handoffs[0].from_node == "discovery"
    assert recovered.handoffs[0].to_node == "verifier"


def test_corrupted_result_json_recovers_without_breaking_state(
    tmp_path: Path,
) -> None:
    db = tmp_path / "graphs.db"
    scheduler = _scheduler(db)
    scheduler.execute_node("discovery")
    store = GraphStore(db)
    store._execute(
        """
        UPDATE mission_graph_node_states
        SET result_json = ?
        WHERE graph_id = ? AND node_id = ?
        """,
            ('{"status": "succeeded", "facts": [not-json]', "graph_fault", "discovery"),
        )
    store._conn.commit()

    restored = _scheduler(db)

    assert restored.state.node_states["discovery"].status == "succeeded"
    assert restored.state.node_states["discovery"].result is None


def test_second_worker_does_not_observe_half_written_handoff(
    tmp_path: Path,
) -> None:
    db = tmp_path / "graphs.db"
    first = _scheduler(db)
    first.execute_node("discovery")
    before = first.handoffs

    second = _scheduler(db)
    store = second._store
    original_execute = store._execute

    def crashing_execute(sql: str, params=()):
        if (
            "DELETE FROM mission_graph_handoffs" in sql
            and "graph_fault" in str(params)
        ):
            raise RuntimeError("simulated handoff crash")
        return original_execute(sql, params)

    store._execute = crashing_execute
    with pytest.raises(RuntimeError, match="simulated handoff crash"):
        second.execute_node("verifier")

    restored = GraphStore(db).load("graph_fault")
    assert tuple(restored["handoffs"]) == before
    assert restored["node_states"]["verifier"].status == "pending"
