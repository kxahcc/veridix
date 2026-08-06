from __future__ import annotations

from services.mission_orchestrator.attack_graph import (
    build_attack_graph,
)


def test_attack_graph_projects_findings_and_facts() -> None:
    graph = build_attack_graph(
        target_ref="https://lab.example.test",
        findings=[
            {
                "endpoint": "/admin",
                "vuln_category": "IDOR",
            },
            {
                "endpoint": "/api/users/2",
                "vuln_category": "IDOR",
            },
        ],
        facts=[
            {
                "subject": "/api/users/2",
                "predicate": "finding",
                "value": "SSRF",
            }
        ],
    )

    payload = graph.to_dict()
    kinds = {node["kind"] for node in payload["nodes"]}
    assert kinds == {"target", "endpoint", "vulnerability"}
    assert len(payload["nodes"]) == 5
    assert any(
        edge["predicate"] == "exposes"
        and edge["target"] == "vuln:IDOR"
        for edge in payload["edges"]
    )
    assert any(
        edge["predicate"] == "exposes"
        and edge["target"] == "vuln:SSRF"
        for edge in payload["edges"]
    )


def test_attack_graph_empty_run_has_target_root() -> None:
    graph = build_attack_graph(
        target_ref="https://lab.example.test",
        findings=[],
    )

    payload = graph.to_dict()

    assert len(payload["nodes"]) == 1
    assert payload["nodes"][0]["kind"] == "target"
    assert payload["edges"] == []
