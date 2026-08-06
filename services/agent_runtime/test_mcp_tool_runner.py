from __future__ import annotations

from services.agent_runtime.kernel.contracts import ExecutionRequest
from services.agent_runtime.kernel.mcp_tool_runner import McpToolRunner


class FakeConnector:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name: str, arguments: dict, *, timeout: float) -> dict:
        self.calls.append((name, arguments))
        if self._error is not None:
            raise self._error
        return self._result


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        action_id="action_mcp",
        run_id="run_mcp",
        tool_ref="mcp.web_lookup",
        input={"target": "https://lab.example.test", "path": "/admin"},
        idempotency_key="run_mcp:mcp.web_lookup:1",
    )


def test_mcp_tool_runner_marks_results_untrusted() -> None:
    connector = FakeConnector(
        result={
            "content": [
                {
                    "type": "text",
                    "text": '{"endpoint": "https://lab.example.test/admin", "status": 200}',
                }
            ],
            "isError": False,
        }
    )
    runner = McpToolRunner(connector)

    result = runner.execute(_request())

    assert result.status == "completed"
    assert result.observations[0]["trust"] == "retrieved_untrusted"
    assert result.observations[0]["mcp"] is True
    assert '"status": 200' in result.stdout
    assert connector.calls[0][0] == "web_lookup"


def test_mcp_tool_runner_surfaces_transport_errors() -> None:
    connector = FakeConnector(error=RuntimeError("connection refused"))
    runner = McpToolRunner(connector)

    result = runner.execute(_request())

    assert result.status == "failed"
    assert "mcp_call_failed" in result.stderr
    assert result.side_effect_state == "known"


def test_mcp_tool_runner_marks_is_error_results_failed() -> None:
    connector = FakeConnector(
        result={
            "content": [{"type": "text", "text": "denied"}],
            "isError": True,
        }
    )
    runner = McpToolRunner(connector)

    result = runner.execute(_request())

    assert result.status == "failed"
    assert "denied" in result.stderr
