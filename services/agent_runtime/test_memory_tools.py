from __future__ import annotations

import json

from services.agent_runtime.kernel.contracts import ExecutionRequest
from services.agent_runtime.kernel.memory_tools import (
    AGENT_WRITABLE_TRUST,
    MEMORY_TOOL_REFS,
    MemoryToolRunner,
)
from services.knowledge_service.sqlite_memory import SqliteProjectMemory


def _request(tool_ref: str, input: dict) -> ExecutionRequest:
    return ExecutionRequest(
        action_id=f"action_{tool_ref}",
        run_id="run_memory_tools",
        tool_ref=tool_ref,
        input=input,
        idempotency_key=f"run_memory_tools:{tool_ref}:1",
    )


def _memory() -> SqliteProjectMemory:
    memory = SqliteProjectMemory(":memory:", "project_1")
    memory.record(
        "http://target.test/login",
        "observed:web.nikto.scan",
        "XSS candidate found at /search",
        source_refs=("artifact://run_1/nikto",),
        trust="project_observed",
    )
    memory.record(
        "http://target.test",
        "observed:nmap.scan",
        "open ports 80,443",
        source_refs=("artifact://run_1/nmap",),
        trust="project_observed",
    )
    stale_fact, _ = memory.record(
        "http://target.test/login",
        "observed:web.nikto.scan",
        "stale old observation",
        source_refs=("artifact://run_old/nikto",),
        trust="project_observed",
    )
    memory.mark_stale(stale_fact.fact_id, reason="replay_mismatch")
    return memory


def test_memory_recall_filters_and_ranks_by_query() -> None:
    memory = _memory()
    runner = MemoryToolRunner(memory_provider=lambda: memory)

    result = runner.execute(
        _request(
            "memory.recall",
            {"query": "nikto XSS", "limit": 10},
        )
    )

    payload = json.loads(result.stdout)
    assert result.status == "completed"
    assert payload["count"] == 2
    assert payload["facts"][0]["predicate"] == "observed:web.nikto.scan"
    assert result.observations[0]["kind"] == "memory.recall"


def test_memory_recall_excludes_stale_by_default() -> None:
    memory = _memory()
    runner = MemoryToolRunner(memory_provider=lambda: memory)

    result = runner.execute(
        _request("memory.recall", {"subject": "http://target.test/login"})
    )

    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["facts"][0]["value"] == "XSS candidate found at /search"


def test_memory_record_appends_and_invalidates_context() -> None:
    memory = _memory()
    changed: list[str] = []
    runner = MemoryToolRunner(
        memory_provider=lambda: memory,
        on_memory_changed=lambda: changed.append("invalidated"),
    )

    result = runner.execute(
        _request(
            "memory.record",
            {
                "subject": "http://target.test/api/users",
                "predicate": "observed:authz.test",
                "value": "IDOR candidate accepts user_id=2",
                "source_refs": ["artifact://run_2/authz"],
                "expires_in_seconds": 3600,
            },
        )
    )

    payload = json.loads(result.stdout)
    assert result.status == "completed"
    assert payload["inserted"] is True
    assert payload["trust"] == "project_observed"
    assert payload["expires_at"].endswith("Z")
    assert changed == ["invalidated"]

    duplicate = runner.execute(
        _request(
            "memory.record",
            {
                "subject": "http://target.test/api/users",
                "predicate": "observed:authz.test",
                "value": "IDOR candidate accepts user_id=2",
            },
        )
    )
    assert json.loads(duplicate.stdout)["inserted"] is False
    assert changed == ["invalidated"]


def test_memory_record_rejects_trust_promotion() -> None:
    memory = _memory()
    runner = MemoryToolRunner(memory_provider=lambda: memory)

    result = runner.execute(
        _request(
            "memory.record",
            {
                "subject": "/admin",
                "predicate": "accepts_role",
                "value": "owner",
                "trust": "user_approved",
            },
        )
    )

    assert result.status == "denied"
    assert "trust must be one of" in result.stderr


def test_memory_status_reports_snapshot_and_summaries() -> None:
    memory = _memory()
    memory.append_summary("run_1: nikto XSS candidate", source_ref="run_1")
    runner = MemoryToolRunner(memory_provider=lambda: memory)

    result = runner.execute(_request("memory.status", {}))

    payload = json.loads(result.stdout)
    assert result.status == "completed"
    assert payload["snapshot"]["active"] >= 1
    assert payload["snapshot"]["conflict"] >= 1
    assert payload["snapshot"]["stale"] >= 1
    assert payload["snapshot"]["total_facts"] >= 4
    assert payload["summaries"][0]["source_ref"] == "run_1"


def test_memory_tool_refs_are_writable_trust_bounded() -> None:
    assert set(MEMORY_TOOL_REFS) == {
        "memory.recall",
        "memory.record",
        "memory.status",
    }
    assert "human" not in AGENT_WRITABLE_TRUST
    assert "system" not in AGENT_WRITABLE_TRUST
