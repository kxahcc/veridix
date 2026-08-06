from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .thread_safe_sqlite import ThreadSafeSqliteConnection


SCHEMA = """
CREATE TABLE IF NOT EXISTS runners (
    runner_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
    provider_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    status TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tools (
    tool_ref TEXT PRIMARY KEY,
    capability TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skills (
    skill_ref TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT '',
    runner TEXT NOT NULL DEFAULT '',
    risk_level TEXT NOT NULL DEFAULT 'L1',
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mcp_servers (
    server_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'local',
    command TEXT NOT NULL DEFAULT '',
    config TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS role_templates (
    template_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class RuntimeRegistry:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = ThreadSafeSqliteConnection(db_path)
        self._conn.executescript(SCHEMA)
        self._ensure_column(
            "providers",
            "config",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        self._ensure_column(
            "mcp_servers",
            "config",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _ensure_column(
        self,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }
        if column not in columns:
            self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def upsert_runner(self, runner_id: str, kind: str, status: str) -> dict[str, Any]:
        return self._upsert(
            "runners",
            ("runner_id", "kind", "status"),
            (runner_id, kind, status),
        )

    def upsert_provider(
        self,
        provider_id: str,
        model: str,
        endpoint: str,
        status: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._upsert(
            "providers",
            ("provider_id", "model", "endpoint", "status", "config"),
            (
                provider_id,
                model,
                endpoint,
                status,
                json.dumps(config or {}, ensure_ascii=True),
            ),
        )

    def delete_provider(self, provider_id: str) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM providers WHERE provider_id = ?",
                (provider_id,),
            )
        return cursor.rowcount > 0

    def delete_skill(self, skill_ref: str) -> bool:
        return self._delete_row("skills", "skill_ref", skill_ref)

    def delete_mcp(self, server_id: str) -> bool:
        return self._delete_row("mcp_servers", "server_id", server_id)

    def delete_tool(self, tool_ref: str) -> bool:
        return self._delete_row("tools", "tool_ref", tool_ref)

    def get_row(self, table: str, key: str) -> dict[str, Any] | None:
        if table not in ("skills", "mcp_servers", "tools"):
            return None
        column = {
            "skills": "skill_ref",
            "mcp_servers": "server_id",
            "tools": "tool_ref",
        }[table]
        row = self._conn.execute(
            f"SELECT * FROM {table} WHERE {column} = ?",
            (key,),
        ).fetchone()
        return dict(row) if row is not None else None

    def _delete_row(self, table: str, column: str, value: str) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                f"DELETE FROM {table} WHERE {column} = ?",
                (value,),
            )
        return cursor.rowcount > 0

    def upsert_tool(
        self,
        tool_ref: str,
        capability: str,
        status: str,
    ) -> dict[str, Any]:
        return self._upsert(
            "tools",
            ("tool_ref", "capability", "status"),
            (tool_ref, capability, status),
        )

    def upsert_skill(
        self,
        skill_ref: str,
        name: str,
        version: str,
        status: str,
        *,
        trigger: str = "",
        runner: str = "",
        risk_level: str = "L1",
    ) -> dict[str, Any]:
        return self._upsert(
            "skills",
            ("skill_ref", "name", "version", "trigger", "runner", "risk_level", "status"),
            (skill_ref, name, version, trigger, runner, risk_level, status),
        )

    def upsert_mcp(
        self,
        server_id: str,
        name: str,
        status: str,
        *,
        kind: str = "local",
        command: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._upsert(
            "mcp_servers",
            ("server_id", "name", "kind", "command", "config", "status"),
            (
                server_id,
                name,
                kind,
                command,
                json.dumps(config or {}, ensure_ascii=True),
                status,
            ),
        )

    def list(self, table: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT * FROM {table} ORDER BY {_order_column(table)}"
        ).fetchall()
        result = [dict(row) for row in rows]
        if table == "providers":
            for item in result:
                try:
                    item["config"] = json.loads(item["config"] or "{}")
                except Exception:
                    item["config"] = {}
        if table == "mcp_servers":
            for item in result:
                try:
                    item["config"] = json.loads(item["config"] or "{}")
                except Exception:
                    item["config"] = {}
        return result

    def get_setting(self, key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except Exception:
            return None

    def set_setting(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=True), now),
            )
        return value

    def list_role_templates(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT template_id, payload FROM role_templates ORDER BY template_id"
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue
            payload["template_id"] = row["template_id"]
            result.append(payload)
        return result

    def save_role_template(
        self,
        template_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO role_templates (template_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (template_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (template_id, json.dumps(payload, ensure_ascii=True), now),
            )
        data = dict(payload)
        data["template_id"] = template_id
        return data

    def get_role_template(self, template_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT template_id, payload FROM role_templates WHERE template_id = ?",
            (template_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except Exception:
            return None
        payload["template_id"] = row["template_id"]
        return payload

    def delete_role_template(self, template_id: str) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM role_templates WHERE template_id = ?",
                (template_id,),
            )
        return cursor.rowcount > 0

    def _upsert(
        self,
        table: str,
        columns: tuple[str, ...],
        values: tuple[str, ...],
    ) -> dict[str, Any]:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column} = excluded.{column}" for column in columns)
        with self._conn:
            self._conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}, updated_at) "
                f"VALUES ({placeholders}, ?) "
                f"ON CONFLICT ({columns[0]}) DO UPDATE SET {updates}, "
                "updated_at = excluded.updated_at",
                (*values, now),
            )
        row = self._conn.execute(
            f"SELECT * FROM {table} WHERE {columns[0]} = ?",
            (values[0],),
        ).fetchone()
        return dict(row)


def _order_column(table: str) -> str:
    return {
        "runners": "runner_id",
        "providers": "provider_id",
        "tools": "tool_ref",
        "skills": "skill_ref",
        "mcp_servers": "server_id",
    }[table]
