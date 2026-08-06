from __future__ import annotations

import time

import runners.web.dom_xss_tester as module
from runners.web.dom_xss_tester import DomXssTesterRunner
from services.agent_runtime.kernel.contracts import ExecutionRequest


def _request(**overrides) -> ExecutionRequest:
    payload = {
        "target": "http://target.example",
        "username": "admin",
        "password": "password",
        "dom_xss_path": "http://target.example/vulnerabilities/xss_d/",
    }
    payload.update(overrides)
    return ExecutionRequest(
        action_id="domxss",
        run_id="domxss",
        tool_ref="web.dom-xss.test",
        input=payload,
        idempotency_key="domxss:1",
        timeout_seconds=30,
    )


def test_dom_xss_tester_normalizes_absolute_endpoint(monkeypatch) -> None:
    runner = DomXssTesterRunner(timeout=5)
    monkeypatch.setattr(module, "_login", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        module,
        "_set_security_low",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(runner, "_browser_check", lambda *args: True)

    result = runner.execute(_request())

    assert result.observations[0]["endpoint"] == (
        "http://target.example/vulnerabilities/xss_d/"
    )
    assert result.observations[0]["vuln_category"] == "XSS"


def test_dom_xss_tester_timeout_returns_error_without_hanging(
    monkeypatch,
) -> None:
    runner = DomXssTesterRunner(timeout=0.2)
    monkeypatch.setattr(module, "_login", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        module,
        "_set_security_low",
        lambda *args, **kwargs: None,
    )

    def slow(*args) -> bool:
        time.sleep(5)
        return True

    monkeypatch.setattr(runner, "_browser_check", slow)

    started = time.monotonic()
    result = runner.execute(_request())

    assert time.monotonic() - started < 3
    assert "exceeded" in str(result.observations[0].get("error") or "")
