from __future__ import annotations

from pathlib import Path

from services.knowledge_service.models import SkillManifest
from services.knowledge_service.skills import SkillRegistry
from services.knowledge_service.skill_retrieval import SkillRetriever


def _skill(
    name: str,
    *,
    trigger: str = "web_discovery",
    tools: tuple[str, ...] = ("web.sqlmap.scan",),
    runner: str = "container",
    content: str = "",
) -> SkillManifest:
    return SkillManifest(
        name=name,
        version="1.0.0",
        trigger=trigger,
        description=f"{name} testing methodology",
        content=content
        or (
            f"Run {name} methodology.\n"
            f"Use SQL injection, parameter testing and evidence replay."
        ),
        required_tools=tools,
        required_runner=runner,
        risk_level="L2",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


def _registry(tmp_path: Path) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        {
            **(
                _skill(
                    "sql-injection",
                    content=(
                        "SQL injection testing with sqlmap, boolean probes "
                        "and evidence replay."
                    ),
                ).__dict__
            )
        }
    )
    registry.register(
        {
            **(
                _skill(
                    "cors-test",
                    content=(
                        "CORS origin reflection and credential leakage "
                        "testing."
                    ),
                ).__dict__
            )
        }
    )
    registry.register(
        {
            **(
                _skill(
                    "host-enum",
                    tools=("host.ssh.probe",),
                    trigger="host",
                    content=(
                        "Host port and service enumeration with SSH probing."
                    ),
                ).__dict__
            )
        }
    )
    return registry


class _TokenEmbedding:
    def embed_query(self, query: str) -> list[float]:
        lowered = query.lower()
        return [
            1.0 if "sql" in lowered or "injection" in lowered else 0.0,
            1.0 if "cors" in lowered else 0.0,
            1.0 if "host" in lowered or "ssh" in lowered else 0.0,
        ]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def search(self, query, chunks):
        vector = self.embed_query(query)
        scored = []
        for chunk in chunks:
            text = chunk.content.lower()
            score = sum(
                value
                for value, keyword in zip(
                    vector,
                    ("sql", "injection", "cors", "host", "ssh"),
                )
                if keyword in text
            )
            scored.append((chunk.chunk_id, float(score)))
        return sorted(scored, key=lambda item: item[1], reverse=True)


class _Rerank:
    def rerank(self, query: str, chunks):
        ordered = sorted(
            chunks,
            key=lambda chunk: (
                "sql" in chunk.content.lower()
                or "injection" in chunk.content.lower()
            ),
            reverse=True,
        )
        return [
            (chunk.chunk_id, float(len(chunks) - index))
            for index, chunk in enumerate(ordered)
        ]


def test_lexical_search_selects_relevant_skill(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    retriever = SkillRetriever(registry=registry, index_path=tmp_path / "s.db")

    result = retriever.search(
        "SQL injection with sqlmap",
        node_type="web_discovery",
        allowed_tools=("web.sqlmap.scan",),
        runner="container",
        limit=2,
    )

    assert result.included
    assert result.included[0].skill.name == "sql-injection"
    assert "bm25" in result.channels


def test_search_filters_missing_tools(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    retriever = SkillRetriever(registry=registry, index_path=tmp_path / "s.db")

    result = retriever.search(
        "host enumeration ssh",
        node_type="host",
        allowed_tools=("nmap.scan",),
        runner="container",
        limit=10,
    )

    assert any(
        item["name"] == "host-enum"
        and "required_tools_missing" in item["reason"]
        for item in result.omitted
    )


def test_index_and_vector_channel(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    retriever = SkillRetriever(
        registry=registry,
        embedding=_TokenEmbedding(),
        index_path=tmp_path / "s.db",
    )

    indexed = retriever.index()
    result = retriever.search(
        "SQL injection",
        node_type="web_discovery",
        allowed_tools=("web.sqlmap.scan",),
        runner="container",
        limit=2,
    )

    assert indexed == 3
    assert "vector" in result.channels
    assert result.included[0].skill.name == "sql-injection"


def test_rerank_channel_and_order(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    retriever = SkillRetriever(
        registry=registry,
        embedding=_TokenEmbedding(),
        rerank=_Rerank(),
        index_path=tmp_path / "s.db",
    )
    retriever.index()

    result = retriever.search(
        "SQL injection",
        node_type="web_discovery",
        allowed_tools=("web.sqlmap.scan",),
        runner="container",
        limit=2,
    )

    assert "rerank" in result.channels
    assert result.included[0].skill.name == "sql-injection"


def test_zero_lexical_match_falls_back_to_available_skills(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    retriever = SkillRetriever(registry=registry, index_path=tmp_path / "s.db")

    result = retriever.search(
        "zzzz no matching term",
        node_type="web_discovery",
        allowed_tools=("web.sqlmap.scan",),
        runner="container",
        limit=2,
    )

    assert result.included
    assert all(candidate.score > 0 for candidate in result.included)
