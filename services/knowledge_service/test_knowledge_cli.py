from __future__ import annotations

import json
import subprocess
import sys


def test_knowledge_cli_add_list_search(tmp_path) -> None:
    db = tmp_path / "knowledge.db"
    added = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.knowledge_service.knowledge_cli",
            "--db",
            str(db),
            "add",
            "--content",
            "admin panel default credentials",
            "--source-ref",
            "cli-test",
            "--subjects",
            "web",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(added.stdout)["added"] == 1

    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.knowledge_service.knowledge_cli",
            "--db",
            str(db),
            "list",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    chunks = json.loads(listed.stdout)
    assert chunks[0]["source_ref"] == "cli-test"

    searched = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.knowledge_service.knowledge_cli",
            "--db",
            str(db),
            "search",
            "admin panel",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    results = json.loads(searched.stdout)
    assert results[0]["chunk_id"]
