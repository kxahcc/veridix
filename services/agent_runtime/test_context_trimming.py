from services.agent_runtime.kernel.context_trimming import (
    BackendSummarizer,
    compact_observation,
    trim_observations,
)


def _obs(index: int, size: int = 100) -> dict:
    return {
        "tool": "web.nikto.scan",
        "arguments": {"target": f"http://target/{index}"},
        "endpoint": f"http://target/{index}",
        "stdout": "x" * size,
        "vuln_category": "Exposure" if index % 2 else "",
    }


def test_trim_keeps_recent_and_returns_summary() -> None:
    observations = tuple(_obs(i, size=4000) for i in range(20))

    kept, summary, removed = trim_observations(
        observations,
        max_context_tokens=4_000,
        reserve_tokens=1_000,
        keep_recent=2,
    )

    assert len(kept) == 2
    assert kept[-1]["arguments"]["target"].endswith("/19")
    assert "Trimmed earlier observations" in summary
    assert len(removed) == 18


def test_trim_does_not_touch_small_context() -> None:
    observations = (_obs(0, size=10), _obs(1, size=10))

    kept, summary, removed = trim_observations(
        observations,
        max_context_tokens=32_000,
        reserve_tokens=4_000,
    )

    assert len(kept) == 2
    assert summary == ""
    assert removed == ()


def test_compact_observation_includes_category() -> None:
    assert "Exposure" in compact_observation(_obs(1))
    assert "http://target/1" in compact_observation(_obs(1))


def test_backend_summarizer_uses_model_finish() -> None:
    class FakeBackend:
        def stream(self, context):
            yield from ()

        def __call__(self, observations):
            return ""

    class FinishBackend:
        def stream(self, context):
            from services.agent_runtime.kernel.contracts import ModelEvent

            yield ModelEvent(type="model.finish", text="scanner observed port 80")

    summarizer = BackendSummarizer(FinishBackend())

    assert summarizer((_obs(1),)) == "scanner observed port 80"
    assert BackendSummarizer(FakeBackend())((_obs(1),)) == ""
