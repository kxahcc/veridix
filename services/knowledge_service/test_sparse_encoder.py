from __future__ import annotations

from services.knowledge_service.sparse_encoder import (
    sparse_encode,
    tokenize,
)


def test_tokenize_mixes_ascii_and_cjk_bigrams() -> None:
    tokens = tokenize("SQL 注入 sqlmap 检测")

    assert "sql" in tokens
    assert "sqlmap" in tokens
    assert any(len(token) == 2 and "\u4e00" <= token[0] <= "\u9fff" for token in tokens)


def test_sparse_encode_is_deterministic_and_ranked() -> None:
    first = sparse_encode("nmap 端口扫描 服务版本")
    second = sparse_encode("nmap 端口扫描 服务版本")

    assert first["indices"] == second["indices"]
    assert first["values"] == second["values"]
    assert first["indices"] == sorted(first["indices"])
    assert all(value > 0 for value in first["values"])
