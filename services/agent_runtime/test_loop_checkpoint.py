from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    LoopSpec,
    OracleResult,
)
from services.agent_runtime.kernel.loop import LoopRunner
from services.agent_runtime.kernel.loops import (
    ScriptedLoopModel,
    WebDiscoveryTool,
    action,
    finish,
)
from services.agent_runtime.kernel.memory import InMemoryCheckpointStore


class RetryOracle:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, state, facts, coverage) -> OracleResult:
        self.calls += 1
        if self.calls == 1:
            return OracleResult(
                status="not_verified",
                reason="first_attempt_incomplete",
            )
        return OracleResult(status="verified", reason="second_attempt")


def test_loop_checkpoint_resumes_without_repeating_tool() -> None:
    store = InMemoryCheckpointStore()
    tool = WebDiscoveryTool(("/",))
    oracle = RetryOracle()
    spec = LoopSpec(
        loop_id="loop_checkpoint",
        profile="web_discovery",
        max_iterations=1,
        allowed_tools=("proxy.list",),
        budget={"known_endpoints": ("/", "/admin")},
    )
    first = LoopRunner(
        spec,
        ScriptedLoopModel(
            [
                action(
                    ActionProposal(
                        action_id="cp_action",
                        tool_ref="proxy.list",
                        input={"path": "/"},
                    )
                )
            ]
        ),
        tool,
        oracle,
        checkpoint_store=store,
        checkpoint_ref="loop_checkpoint",
    )

    first_result = first.run()

    assert first_result.status == "inconclusive"
    assert len(tool.executions) == 1
    checkpoint = store.load("loop_checkpoint")
    assert checkpoint is not None

    resume_spec = LoopSpec(
        loop_id="loop_checkpoint",
        profile="web_discovery",
        max_iterations=2,
        allowed_tools=("proxy.list",),
        budget={"known_endpoints": ("/", "/admin")},
    )
    resumed = LoopRunner(
        resume_spec,
        ScriptedLoopModel([finish("resume complete")]),
        tool,
        oracle,
        checkpoint_store=store,
        checkpoint_ref="loop_checkpoint",
    )
    resumed.restore_checkpoint(checkpoint)

    second_result = resumed.run(resumed=True)

    assert second_result.status == "succeeded"
    assert len(tool.executions) == 1
    assert resumed._iterations == 2
    assert len(resumed._observation_history) == 1
    assert resumed._state().observation_history == (
        {"endpoint": "/", "method": "GET", "status": 200},
    )
