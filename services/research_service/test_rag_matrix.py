from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from services.research_service.rag_matrix_cli import run_rag_matrix


def test_offline_rag_matrix_lexical_hits_and_embedding_degraded() -> None:
    payload = run_rag_matrix(["fixture-model"])

    rows = {row["rag_level"]: row for row in payload["rows"]}
    assert rows["lexical"]["hit_rate"] == 1.0
    assert rows["lexical"]["degraded"] == 0
    assert rows["embedding"]["degraded"] == 3
    assert rows["embedding_rerank"]["degraded"] == 3
    assert rows["qdrant_hybrid"]["hit_rate"] == 1.0
    assert len(payload["rows"]) == 4


def test_rag_matrix_cli_writes_report(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    out = tmp_path / "rag-matrix.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.research_service.rag_matrix_cli",
            "--models",
            "fixture-a,fixture-b",
            "--out",
            str(out),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["models"] == ["fixture-a", "fixture-b"]
    assert len(report["rows"]) == 8
    assert out.exists()
