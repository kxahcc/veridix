from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .thread_safe_sqlite import ThreadSafeSqliteConnection
from .contracts import AgentEvent
from datetime import datetime, timedelta, timezone

from .domain import (
    ApprovalRequest,
    LeaseRecord,
    Mission,
    Project,
    RunState,
    TargetProfile,
    new_id,
    utc_now,
)
from .event_store import CommandStore, EventStore
from .outbox import OutboxStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS target_profiles (
    target_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    url TEXT NOT NULL,
    allowed TEXT NOT NULL,
    excluded TEXT NOT NULL,
    authorization TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS missions (
    mission_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    spec TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    source_run_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool_ref TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    state TEXT NOT NULL,
    policy_rule TEXT NOT NULL,
    reason TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    budget_reserved INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_leases (
    worker_id TEXT PRIMARY KEY,
    lease_until TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connector_status (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL
);
"""


class ControlStore:
    def __init__(
        self,
        events: EventStore,
        commands: CommandStore,
        db_path: str | Path = ":memory:",
        outbox: OutboxStore | None = None,
    ) -> None:
        self._events = events
        self._commands = commands
        self._outbox = outbox
        self._conn = ThreadSafeSqliteConnection(db_path)
        self._conn.executescript(SCHEMA)
        self._ensure_column("projects", "owner", "TEXT NOT NULL DEFAULT 'local'")
        self._ensure_column("runs", "source_run_id", "TEXT")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def create_project(self, name: str, owner: str = "local") -> Project:
        project = Project(project_id=new_id("project"), name=name, owner=owner)
        with self._conn:
            self._conn.execute(
                "INSERT INTO projects (project_id, name, owner, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    project.project_id,
                    project.name,
                    project.owner,
                    project.created_at,
                ),
            )
        return project

    def list_projects(self) -> list[Project]:
        rows = self._conn.execute(
            "SELECT * FROM projects ORDER BY created_at"
        ).fetchall()
        return [Project(**dict(row)) for row in rows]

    def get_project(self, project_id: str) -> Project:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"project {project_id} not found")
        return Project(**dict(row))

    def save_connector_status(
        self,
        name: str,
        url: str,
        status: str,
        checked_at: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO connector_status
                    (name, url, status, checked_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, url, status, checked_at),
            )

    def get_connector_status(
        self,
        name: str,
    ) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM connector_status WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row) if row is not None else None

    def create_target(
        self,
        project_id: str,
        url: str,
        *,
        allowed: tuple[str, ...] = (),
        excluded: tuple[str, ...] = (),
        authorization: str = "authorized",
    ) -> TargetProfile:
        self.get_project(project_id)
        target = TargetProfile(
            target_id=new_id("target"),
            project_id=project_id,
            url=url,
            allowed=list(allowed),
            excluded=list(excluded),
            authorization=authorization,
        )
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO target_profiles
                    (target_id, project_id, url, allowed, excluded,
                     authorization, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target.target_id,
                    target.project_id,
                    target.url,
                    json.dumps(target.allowed),
                    json.dumps(target.excluded),
                    target.authorization,
                    target.created_at,
                ),
            )
        return target

    def create_mission(
        self,
        project_id: str,
        name: str,
        spec: dict | None = None,
    ) -> Mission:
        self.get_project(project_id)
        mission = Mission(
            mission_id=new_id("mission"),
            project_id=project_id,
            name=name,
            spec=spec or {},
        )
        with self._conn:
            self._conn.execute(
                "INSERT INTO missions (mission_id, project_id, name, spec, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    mission.mission_id,
                    mission.project_id,
                    mission.name,
                    json.dumps(mission.spec, ensure_ascii=True),
                    mission.created_at,
                ),
            )
        return mission

    def get_mission(self, mission_id: str) -> Mission:
        row = self._conn.execute(
            "SELECT * FROM missions WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"mission {mission_id} not found")
        data = dict(row)
        data["spec"] = json.loads(data["spec"])
        return Mission(**data)

    def list_all_missions(self) -> list[Mission]:
        rows = self._conn.execute(
            "SELECT * FROM missions ORDER BY created_at DESC"
        ).fetchall()
        missions: list[Mission] = []
        for row in rows:
            data = dict(row)
            data["spec"] = json.loads(data["spec"])
            missions.append(Mission(**data))
        return missions

    def get_target(self, target_id: str) -> TargetProfile:
        row = self._conn.execute(
            "SELECT * FROM target_profiles WHERE target_id = ?",
            (target_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"target {target_id} not found")
        data = dict(row)
        data["allowed"] = json.loads(data["allowed"])
        data["excluded"] = json.loads(data["excluded"])
        return TargetProfile(**data)

    def list_targets(self, project_id: str) -> list[TargetProfile]:
        rows = self._conn.execute(
            "SELECT * FROM target_profiles WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        targets: list[TargetProfile] = []
        for row in rows:
            data = dict(row)
            data["allowed"] = json.loads(data["allowed"])
            data["excluded"] = json.loads(data["excluded"])
            targets.append(TargetProfile(**data))
        return targets

    def create_run(
        self,
        mission_id: str,
        run_id: str | None = None,
        source_run_id: str | None = None,
    ) -> RunState:
        row = self._conn.execute(
            "SELECT * FROM missions WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"mission {mission_id} not found")
        run_id = run_id or new_id("run")
        created_at = utc_now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs (run_id, mission_id, source_run_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (run_id, mission_id, source_run_id, created_at),
            )
        self._events.append(
            AgentEvent(
                event_id=f"{run_id}:queued",
                event_type="run.queued",
                stream_id=run_id,
                run_id=run_id,
                actor="api/control",
                occurred_at=created_at,
                payload={"mission_id": mission_id},
            )
        )
        if self._outbox is not None:
            self._outbox.enqueue(run_id, "run.queued", {"mission_id": mission_id})
        return self.get_run(run_id, created_at=created_at)

    def get_run(self, run_id: str, *, created_at: str | None = None) -> RunState:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"run {run_id} not found")
        events = self._events.replay(run_id)
        from .projection import project_run

        projection = project_run(events)
        return RunState(
            run_id=run_id,
            mission_id=row["mission_id"],
            source_run_id=row["source_run_id"],
            status=projection["status"],
            event_count=projection["event_count"],
            observations=projection["observations"],
            stop_reason=projection["stop_reason"],
            created_at=created_at or row["created_at"],
        )

    def list_runs(self, mission_id: str) -> list[RunState]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE mission_id = ? ORDER BY created_at",
            (mission_id,),
        ).fetchall()
        return [self.get_run(row["run_id"]) for row in rows]

    def list_all_runs(self) -> list[RunState]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC"
        ).fetchall()
        return [self.get_run(row["run_id"]) for row in rows]

    def request_approval(
        self,
        run_id: str,
        tool_ref: str,
        risk_level: str,
        *,
        policy_rule: str,
        reason: str = "",
        budget_reserved: int = 1,
        approval_id: str | None = None,
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            approval_id=approval_id or new_id("approval"),
            run_id=run_id,
            tool_ref=tool_ref,
            risk_level=risk_level,
            state="requested",
            policy_rule=policy_rule,
            reason=reason,
            budget_reserved=budget_reserved,
        )
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO approvals
                    (approval_id, run_id, tool_ref, risk_level, state,
                     policy_rule, reason, requested_at, decided_at,
                     decided_by, budget_reserved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    approval.approval_id,
                    approval.run_id,
                    approval.tool_ref,
                    approval.risk_level,
                    approval.state,
                    approval.policy_rule,
                    approval.reason,
                    approval.requested_at,
                    approval.budget_reserved,
                ),
            )
        return approval

    def get_approval(self, approval_id: str) -> ApprovalRequest:
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"approval {approval_id} not found")
        return ApprovalRequest(**dict(row))

    def list_approvals(self, run_id: str) -> list[ApprovalRequest]:
        rows = self._conn.execute(
            "SELECT * FROM approvals WHERE run_id = ? ORDER BY requested_at",
            (run_id,),
        ).fetchall()
        return [ApprovalRequest(**dict(row)) for row in rows]

    def decide_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        decided_by: str,
        reason: str = "",
    ) -> ApprovalRequest:
        state = "approved" if approved else "rejected"
        with self._conn:
            self._conn.execute(
                """
                UPDATE approvals
                SET state = ?, decided_at = ?, decided_by = ?, reason = ?
                WHERE approval_id = ?
                """,
                (state, utc_now(), decided_by, reason, approval_id),
            )
        return self.get_approval(approval_id)

    def upsert_lease(self, worker_id: str, lease_seconds: int) -> LeaseRecord:
        lease_until = (
            datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        now = utc_now()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO worker_leases (worker_id, lease_until, last_seen_at)
                VALUES (?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    lease_until = excluded.lease_until,
                    last_seen_at = excluded.last_seen_at
                """,
                (worker_id, lease_until, now),
            )
        return LeaseRecord(worker_id=worker_id, lease_until=lease_until, last_seen_at=now)

    def delete_project(self, project_id: str) -> list[str]:
        self.get_project(project_id)
        rows = self._conn.execute(
            "SELECT run_id FROM runs WHERE mission_id IN "
            "(SELECT mission_id FROM missions WHERE project_id = ?)",
            (project_id,),
        ).fetchall()
        run_ids = [str(row["run_id"]) for row in rows]
        for run_id in run_ids:
            self._events.delete_stream(run_id)
        with self._conn:
            self._conn.execute(
                "DELETE FROM runs WHERE mission_id IN "
                "(SELECT mission_id FROM missions WHERE project_id = ?)",
                (project_id,),
            )
            self._conn.execute(
                "DELETE FROM missions WHERE project_id = ?",
                (project_id,),
            )
            self._conn.execute(
                "DELETE FROM target_profiles WHERE project_id = ?",
                (project_id,),
            )
            self._conn.execute(
                "DELETE FROM projects WHERE project_id = ?",
                (project_id,),
            )
        return run_ids

    def get_lease(self, worker_id: str) -> LeaseRecord | None:
        row = self._conn.execute(
            "SELECT * FROM worker_leases WHERE worker_id = ?",
            (worker_id,),
        ).fetchone()
        return LeaseRecord(**dict(row)) if row is not None else None
