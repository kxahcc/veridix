from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_alembic_upgrade_creates_core_tables(tmp_path) -> None:
    services_dir = Path(__file__).resolve().parents[1] / "services"
    db_path = tmp_path / "migrated.sqlite3"
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(services_dir),
        env=env,
        check=True,
        capture_output=True,
    )

    conn = sqlite3.connect(str(db_path))
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "alembic_version" in tables
    assert "events" in tables
    assert "commands" in tables
