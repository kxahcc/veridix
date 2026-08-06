from __future__ import annotations

from pathlib import Path
from typing import Any

from .graph_store import KnowledgeGraphStore
from .models import KnowledgeChunk


def create_knowledge_graph(
    config: dict[str, Any] | None,
    *,
    runtime_dir: str | Path,
) -> KnowledgeGraphStore:
    """Build the knowledge graph backend from retrieval config.

    SQLite is the desktop default; Neo4j is the server/team option and
    requires the optional ``neo4j`` driver to be installed.
    """
    config = config or {}
    backend = str(config.get("type") or "sqlite")
    if backend == "sqlite":
        return KnowledgeGraphStore(
            str(Path(runtime_dir) / "knowledge-graph.db")
        )
    if backend == "neo4j":
        if not config.get("uri"):
            raise ValueError(
                "neo4j graph backend requires uri in graph_backend config"
            )
        return Neo4jKnowledgeGraphAdapter(
            uri=str(config["uri"]),
            user=str(config.get("user") or "neo4j"),
            password=str(config.get("password") or ""),
            database=str(config.get("database") or "neo4j"),
        )
    raise ValueError(f"unsupported knowledge graph backend: {backend}")


class Neo4jKnowledgeGraphAdapter(KnowledgeGraphStore):
    """Bolt adapter implementing the KnowledgeGraphStore surface on Neo4j."""

    def __init__(
        self,
        *,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
    ) -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError as error:
            raise RuntimeError(
                "neo4j graph backend requires the optional 'neo4j' "
                "package; install it or use type=sqlite"
            ) from error
        self._driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
        )
        self._database = database

    def close(self) -> None:
        self._driver.close()

    def register_chunk_graph(self, chunk: KnowledgeChunk) -> None:
        graph = chunk.graph or {}
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        with self._driver.session(database=self._database) as session:
            session.execute_write(
                _merge_chunk_and_nodes,
                chunk.chunk_id,
                nodes,
                edges,
            )

    def nodes_for_terms(
        self,
        terms: tuple[str, ...],
        *,
        limit: int = 20,
    ) -> list[str]:
        with self._driver.session(database=self._database) as session:
            return session.execute_read(
                _match_nodes_for_terms,
                list(terms),
                limit,
            )

    def chunk_count(self) -> int:
        with self._driver.session(database=self._database) as session:
            return session.execute_read(_count_chunks)

    def neighbors(
        self,
        node_ids: tuple[str, ...],
        *,
        depth: int = 1,
        limit: int = 20,
    ) -> list[str]:
        with self._driver.session(database=self._database) as session:
            return session.execute_read(
                _match_neighbors,
                list(node_ids),
                max(1, depth),
                limit,
            )

    def chunk_ids_for_nodes(
        self,
        node_ids: tuple[str, ...],
    ) -> dict[str, list[str]]:
        with self._driver.session(database=self._database) as session:
            return session.execute_read(
                _match_chunks_for_nodes,
                list(node_ids),
            )

    def path_labels(
        self,
        node_ids: tuple[str, ...],
    ) -> dict[str, str]:
        with self._driver.session(database=self._database) as session:
            return session.execute_read(
                _match_labels,
                list(node_ids),
            )


def _merge_chunk_and_nodes(
    tx,
    chunk_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    tx.run(
        "MERGE (c:KnowledgeChunk {id: $chunk_id})",
        chunk_id=chunk_id,
    )
    for node in nodes:
        tx.run(
            """
            MERGE (n:KnowledgeNode {id: $node_id})
            ON CREATE SET n.label = $label, n.node_type = $node_type
            ON MATCH SET n.label = $label, n.node_type = $node_type
            WITH n
            MATCH (c:KnowledgeChunk {id: $chunk_id})
            MERGE (c)-[:LINKS_TO]->(n)
            """,
            node_id=str(node["id"]),
            label=str(node.get("label", node["id"])),
            node_type=str(node.get("type", "entity")),
            chunk_id=chunk_id,
        )
    for edge in edges:
        tx.run(
            """
            MATCH (a:KnowledgeNode {id: $source})
            MATCH (b:KnowledgeNode {id: $target})
            MERGE (a)-[:RELATED {predicate: $predicate}]->(b)
            """,
            source=str(edge["source"]),
            target=str(edge["target"]),
            predicate=str(edge.get("predicate", "related_to")),
        )


def _match_nodes_for_terms(tx, terms: list[str], limit: int) -> list[str]:
    clauses = " OR ".join(
        [
            (
                f"toLower(n.label) CONTAINS $t{index} "
                f"OR toLower(n.id) CONTAINS $t{index}"
            )
            for index in range(len(terms))
        ]
    )
    params = {f"t{index}": term.lower() for index, term in enumerate(terms)}
    query = (
        f"MATCH (n:KnowledgeNode) WHERE {clauses} "
        "RETURN n.id AS node_id LIMIT $limit"
    )
    params["limit"] = limit
    return [row["node_id"] for row in tx.run(query, **params)]


def _match_neighbors(
    tx,
    node_ids: list[str],
    depth: int,
    limit: int,
) -> list[str]:
    query = (
        "MATCH (start:KnowledgeNode) "
        "WHERE start.id IN $ids "
        f"MATCH (start)-[*1..{depth}]-(peer:KnowledgeNode) "
        "RETURN DISTINCT peer.id AS node_id LIMIT $limit"
    )
    return [
        row["node_id"]
        for row in tx.run(
            query,
            ids=node_ids,
            limit=limit,
        )
    ]


def _match_chunks_for_nodes(
    tx,
    node_ids: list[str],
) -> dict[str, list[str]]:
    rows = tx.run(
        """
        MATCH (n:KnowledgeNode)<-[:LINKS_TO]-(c:KnowledgeChunk)
        WHERE n.id IN $ids
        RETURN n.id AS node_id, c.id AS chunk_id
        """,
        ids=node_ids,
    )
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row["node_id"], []).append(row["chunk_id"])
    return result


def _match_labels(tx, node_ids: list[str]) -> dict[str, str]:
    rows = tx.run(
        """
        MATCH (n:KnowledgeNode)
        WHERE n.id IN $ids
        RETURN n.id AS node_id, n.label AS label
        """,
        ids=node_ids,
    )
    return {row["node_id"]: row["label"] for row in rows}


def _count_chunks(tx) -> int:
    row = tx.run(
        "MATCH (c:KnowledgeChunk) RETURN count(c) AS count"
    ).single()
    return int(row["count"])
