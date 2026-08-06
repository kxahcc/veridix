from __future__ import annotations

from pathlib import Path

from services.agent_runtime.kernel.contracts import ExecutionRequest
from services.agent_runtime.kernel.contracts import (
    AgentRunSpec,
    ModelEvent,
    ToolCall,
)
from services.agent_runtime.kernel.kernel import AgentKernel
from services.agent_runtime.kernel.memory import (
    InMemoryCheckpointStore,
    InMemoryEventSink,
)
from services.agent_runtime.kernel.tool_broker import ToolBroker
from services.agent_runtime.kernel.skill_bundle_runner import (
    SkillBundleRunner,
)


def _request(**overrides) -> ExecutionRequest:
    payload = {
        "action_id": "action_1",
        "run_id": "run_1",
        "tool_ref": "skill.read",
        "input": {
            "skill_ref": "veridix-redteam-orchestration",
            "path": "references/evidence-gate.md",
        },
        "idempotency_key": "read:1",
        "timeout_seconds": 5,
    }
    payload.update(overrides)
    return ExecutionRequest(**payload)


def _seed(tmp_path: Path) -> Path:
    package = (
        tmp_path
        / "skills"
        / "builtin"
        / "veridix-redteam-orchestration"
        / "references"
    )
    package.mkdir(parents=True)
    (package / "evidence-gate.md").write_text(
        "# Evidence Gate\n\nEvery finding must be reproducible.",
        encoding="utf-8",
    )
    (package / "secret.txt").write_text("do not read", encoding="utf-8")
    (package / "logo.png").write_bytes(b"not text")
    return tmp_path


def test_reads_skill_bundle_file(tmp_path: Path) -> None:
    runner = SkillBundleRunner(_seed(tmp_path))

    result = runner.execute(_request())

    assert result.status == "completed"
    assert "Every finding must be reproducible" in result.stdout


def test_rejects_path_traversal(tmp_path: Path) -> None:
    runner = SkillBundleRunner(_seed(tmp_path))

    result = runner.execute(
        _request(
            input={
                "skill_ref": "veridix-redteam-orchestration",
                "path": "../secret.txt",
            }
        )
    )

    assert result.status == "failed"
    assert "path_escapes_skill" in result.stderr


def test_rejects_unknown_file_and_binary_suffix(tmp_path: Path) -> None:
    runner = SkillBundleRunner(_seed(tmp_path))

    missing = runner.execute(
        _request(
            input={
                "skill_ref": "veridix-redteam-orchestration",
                "path": "references/missing.md",
            }
        )
    )
    binary = runner.execute(
        _request(
            input={
                "skill_ref": "veridix-redteam-orchestration",
                "path": "references/logo.png",
            }
        )
    )

    assert missing.status == "failed"
    assert "file_not_found" in missing.stderr
    assert binary.status == "failed"
    assert "unsupported_file_type" in binary.stderr


def test_rejects_invalid_skill_ref(tmp_path: Path) -> None:
    runner = SkillBundleRunner(_seed(tmp_path))

    result = runner.execute(
        _request(
            input={
                "skill_ref": "../evil",
                "path": "SKILL.md",
            }
        )
    )

    assert result.status == "failed"
    assert "invalid_skill_ref" in result.stderr


def test_rejects_oversized_file(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    package = (
        seed
        / "skills"
        / "builtin"
        / "veridix-redteam-orchestration"
        / "references"
    )
    (package / "large.md").write_text(
        "x" * 70_000,
        encoding="utf-8",
    )
    runner = SkillBundleRunner(seed, max_bytes=4096)

    result = runner.execute(
        _request(
            input={
                "skill_ref": "veridix-redteam-orchestration",
                "path": "references/large.md",
            }
        )
    )

    assert result.status == "failed"
    assert "file_too_large" in result.stderr


def test_tool_broker_allows_and_executes_skill_read(tmp_path: Path) -> None:
    runner = SkillBundleRunner(_seed(tmp_path))
    broker = ToolBroker(runner)
    spec = AgentRunSpec(
        run_id="run_skill",
        mission_id="mission_1",
        target_ref="http://lab.test",
        behavior_snapshot="b1",
        allowed_targets=("http://lab.test",),
        allowed_tools=("skill.read", "run.finish"),
        max_turns=2,
    )

    decision = broker.authorize(
        ToolCall(
            id="call_1",
            name="skill.read",
            arguments={
                "skill_ref": "veridix-redteam-orchestration",
                "path": "references/evidence-gate.md",
            },
        ),
        spec,
    )
    outcome = broker.execute(
        ExecutionRequest(
            action_id="a1",
            run_id="run_skill",
            tool_ref="skill.read",
            input={
                "skill_ref": "veridix-redteam-orchestration",
                "path": "references/evidence-gate.md",
            },
            idempotency_key="read:2",
        )
    )

    assert decision.allowed is True
    assert outcome.result.status == "completed"
    assert "reproducible" in outcome.result.stdout


def test_agent_kernel_executes_skill_read_end_to_end(tmp_path: Path) -> None:
    seed = _seed(tmp_path)

    class SkillReadBackend:
        def __init__(self) -> None:
            self._calls = 0

        def stream(self, context):
            if self._calls == 0:
                self._calls += 1
                yield ModelEvent(
                    type="model.tool_call",
                    tool_call=ToolCall(
                        id="call_skill_read",
                        name="skill.read",
                        arguments={
                            "skill_ref": "veridix-redteam-orchestration",
                            "path": "references/evidence-gate.md",
                        },
                    ),
                )
                return
            yield ModelEvent(type="model.finish", text="done")

    events = InMemoryEventSink()
    spec = AgentRunSpec(
        run_id="run_skill_read",
        mission_id="mission_1",
        target_ref="http://lab.test",
        behavior_snapshot="b1",
        allowed_targets=("http://lab.test",),
        allowed_tools=("skill.read", "run.finish"),
        max_turns=2,
    )
    kernel = AgentKernel(
        spec,
        SkillReadBackend(),
        ToolBroker(SkillBundleRunner(seed)),
        events,
        InMemoryCheckpointStore(),
    )

    kernel.start()
    status = kernel.submit("read evidence gate checklist")

    assert status.value == "succeeded"
    observations = [
        event
        for event in events.replay(spec.run_id)
        if event.event_type == "observation.ingested"
    ]
    assert observations
    assert "reproducible" in str(observations[-1].payload.get("stdout", ""))
