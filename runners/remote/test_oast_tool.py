from __future__ import annotations

import json

from services.agent_runtime.kernel.contracts import ExecutionRequest

from runners.remote.oast import OastStore
from runners.remote.oast_tool import OastToolRunner


def _request(tool_ref: str, input: dict) -> ExecutionRequest:
    return ExecutionRequest(
        action_id=f"action_{tool_ref}",
        run_id="run_oast_tool",
        tool_ref=tool_ref,
        input=input,
        idempotency_key=f"run_oast_tool:{tool_ref}:1",
    )


def test_oast_create_and_check_flow() -> None:
    store = OastStore(":memory:")
    runner = OastToolRunner(
        store=store,
        base_url="http://127.0.0.1:8791",
    )

    created = runner.execute(
        _request("oast.create", {"purpose": "blind-ssrf"})
    )
    payload = json.loads(created.stdout)

    assert created.status == "completed"
    assert payload["callback_url"].startswith(
        "http://127.0.0.1:8791/callback/"
    )
    assert payload["purpose"] == "blind-ssrf"
    assert created.observations[0]["kind"] == "oast_token"

    before = runner.execute(
        _request("oast.check", {"token": payload["token"]})
    )
    assert before.observations == ()

    store.redeem(
        payload["token"],
        source="http",
        payload={"path": "/callback"},
    )
    after = runner.execute(
        _request("oast.check", {"token": payload["token"]})
    )

    assert after.observations[0]["kind"] == "oast_callback"
    assert after.observations[0]["payload"]["path"] == "/callback"

    runner.close()


def test_oast_unknown_tool_is_denied() -> None:
    runner = OastToolRunner()

    result = runner.execute(_request("oast.unknown", {}))

    assert result.status == "denied"
    runner.close()
