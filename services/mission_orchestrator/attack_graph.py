from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AttackGraphNode:
    node_id: str
    label: str
    kind: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AttackGraphEdge:
    source: str
    target: str
    predicate: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AttackGraph:
    nodes: tuple[AttackGraphNode, ...]
    edges: tuple[AttackGraphEdge, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "id": node.node_id,
                    "label": node.label,
                    "kind": node.kind,
                    "properties": node.properties,
                }
                for node in self.nodes
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "predicate": edge.predicate,
                    "properties": edge.properties,
                }
                for edge in self.edges
            ],
        }


def build_attack_graph(
    *,
    target_ref: str,
    findings: list[dict[str, Any]],
    facts: list[dict[str, Any]] | None = None,
) -> AttackGraph:
    """Project findings and facts into a target -> endpoint -> vulnerability graph."""
    nodes: dict[str, AttackGraphNode] = {
        f"target://{target_ref}": AttackGraphNode(
            node_id=f"target://{target_ref}",
            label=target_ref,
            kind="target",
        )
    }
    edges: list[AttackGraphEdge] = []
    endpoints: set[str] = set()
    vulnerabilities: dict[str, set[str]] = {}

    for finding in findings:
        endpoint = str(
            finding.get("endpoint")
            or finding.get("url")
            or target_ref
        )
        category = str(
            finding.get("vuln_category")
            or finding.get("category")
            or "unknown"
        )
        endpoints.add(endpoint)
        vulnerabilities.setdefault(category, set()).add(endpoint)

    for fact in facts or []:
        if fact.get("predicate") != "finding":
            continue
        subject = str(fact.get("subject") or target_ref)
        category = str(fact.get("value") or "unknown")
        endpoints.add(subject)
        vulnerabilities.setdefault(category, set()).add(subject)

    for endpoint in sorted(endpoints):
        node_id = f"endpoint://{endpoint}"
        nodes[node_id] = AttackGraphNode(
            node_id=node_id,
            label=endpoint,
            kind="endpoint",
        )
        edges.append(
            AttackGraphEdge(
                source=f"target://{target_ref}",
                target=node_id,
                predicate="has_endpoint",
            )
        )
    for category, subjects in sorted(vulnerabilities.items()):
        node_id = f"vuln:{category}"
        nodes[node_id] = AttackGraphNode(
            node_id=node_id,
            label=category,
            kind="vulnerability",
        )
        for endpoint in sorted(subjects):
            edges.append(
                AttackGraphEdge(
                    source=f"endpoint://{endpoint}",
                    target=node_id,
                    predicate="exposes",
                )
            )
    return AttackGraph(
        nodes=tuple(nodes.values()),
        edges=tuple(edges),
    )
