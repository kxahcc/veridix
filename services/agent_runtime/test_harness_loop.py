from __future__ import annotations

from services.agent_runtime.kernel.contracts import (
    ActionProposal,
    LoopSpec,
    NodeSpec,
)
from services.agent_runtime.kernel.harness import (
    HarnessBuilder,
    KnowledgeEntry,
    ProviderCapability,
    SkillEntry,
    ToolEntry,
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


def test_harness_projection_omits_with_reasons() -> None:
    node = NodeSpec(
        node_id="discovery",
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id="loop_discovery",
            profile="web_discovery",
            allowed_tools=("shell.probe", "browser.open"),
        ),
        allowed_tools=("shell.probe", "browser.open"),
        harness_profile="web",
        knowledge_view="mission",
        oracle_ref="coverage_oracle",
    )
    builder = HarnessBuilder(
        tools={
            "shell.probe": ToolEntry(name="shell.probe"),
            "browser.open": ToolEntry(
                name="browser.open",
                required_capability="streaming",
            ),
        },
        skills={
            "web_capture": SkillEntry(trigger="web", version="1.2"),
            "legacy_capture": SkillEntry(trigger="web", version="0.9"),
        },
        knowledge={
            "docs/admin": KnowledgeEntry(ref="docs/admin", subjects=("/admin",)),
            "external/blog": KnowledgeEntry(
                ref="external/blog",
                subjects=("/admin",),
                trust="retrieved_untrusted",
            ),
        },
    )
    provider = ProviderCapability(
        model_names=("fixture-model",),
        health="ok",
        streaming=False,
    )

    harness, projection = builder.build(
        node,
        provider,
        target_ref="https://lab.example.test",
        auth_context_ref="auth://fixture",
        scope_hash="scope_hash",
        known_subjects=frozenset({"/admin"}),
    )

    assert projection.included_tools == ("shell.probe",)
    reasons = {item["name"]: item["reason"] for item in projection.omitted}
    assert reasons["browser.open"] == "provider_lacks_capability:streaming"
    assert reasons["legacy_capture"] == "skill_version_mismatch"
    assert reasons["external/blog"] == "trust_denied"
    assert harness.tool_projection_digest


def test_harness_projection_includes_native_system_tools() -> None:
    node = NodeSpec(
        node_id="native",
        node_type="loop",
        loop_spec=LoopSpec(
            loop_id="loop_native",
            profile="default",
            allowed_tools=(
                "run.finish",
                "skill.read",
                "memory.recall",
                "memory.record",
                "memory.status",
            ),
        ),
        allowed_tools=(
            "run.finish",
            "skill.read",
            "memory.recall",
            "memory.record",
            "memory.status",
        ),
        harness_profile="default",
        oracle_ref="coverage_oracle",
    )
    builder = HarnessBuilder(tools={})

    harness, projection = builder.build(
        node,
        ProviderCapability(model_names=("fixture-model",), health="ok"),
        target_ref="https://lab.example.test",
        auth_context_ref="auth://fixture",
        scope_hash="scope_hash",
    )

    assert set(projection.included_tools) == {
        "run.finish",
        "skill.read",
        "memory.recall",
        "memory.record",
        "memory.status",
    }
    assert not any(
        item.get("kind") == "tool"
        for item in projection.omitted
    )
    assert harness.tool_projection_digest


def test_web_discovery_loop_ends_verified_by_oracle() -> None:
    spec = LoopSpec(
        loop_id="loop_discovery",
        profile="web_discovery",
        max_iterations=4,
        allowed_tools=("proxy.list",),
        stop_on_coverage=1.0,
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

    assert result.status == "succeeded"
    assert result.stop_reason == "oracle_verified"
    assert result.oracle_result is not None
    assert result.oracle_result.status == "verified"
    assert len(result.facts) == 3


def test_verifier_loop_requires_oracle_and_proof() -> None:
    spec = LoopSpec(
        loop_id="loop_verifier",
        profile="verifier",
        max_iterations=4,
        allowed_tools=("evidence.replay",),
    )
    model = ScriptedLoopModel(
        [
            action(
                ActionProposal(
                    action_id="v1",
                    tool_ref="evidence.replay",
                    input={"candidate": "/admin"},
                )
            ),
            finish("verify done"),
        ]
    )
    verified = LoopRunner(
        spec,
        model,
        VerifierTool({"/admin": "replay://proof/admin"}),
        VerifierOracle(),
    ).run(hypotheses=("/admin",))
    ghost = LoopRunner(
        spec,
        ScriptedLoopModel(
            [
                action(
                    ActionProposal(
                        action_id="g1",
                        tool_ref="evidence.replay",
                        input={"candidate": "/ghost"},
                    )
                ),
                finish("no proof"),
            ]
        ),
        VerifierTool({}),
        VerifierOracle(),
    ).run(hypotheses=("/ghost",))

    assert verified.status == "succeeded"
    assert any(fact.predicate == "replay_proof" for fact in verified.facts)
    assert ghost.status == "inconclusive"
    assert ghost.stop_reason == "oracle_not_verified"


def test_model_looping_triggers_replan_suggestion() -> None:
    spec = LoopSpec(
        loop_id="loop_looping",
        profile="stub",
        max_iterations=6,
        allowed_tools=("stub.noop",),
    )
    proposal = ActionProposal(
        action_id="l1",
        tool_ref="stub.noop",
        input={"key": "same"},
    )
    model = ScriptedLoopModel([action(proposal), action(proposal), action(proposal), finish("done")])

    class NoopTool:
        def execute(self, proposal, *, idempotency_key):
            from services.agent_runtime.kernel.contracts import LoopToolResult

            return LoopToolResult(status="completed")

    runner = LoopRunner(spec, model, NoopTool(), WebDiscoveryOracle())
    result = runner.run()

    assert result.status == "inconclusive"
    assert result.stop_reason == "model_looping"
    assert any(event.event_type == "loop.replan.suggested" for event in runner.events)
