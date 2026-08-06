from __future__ import annotations

from services.control_plane.app.thread_safe_sqlite import SqliteResult

import json
import sqlite3
import threading
from pathlib import Path

from .models import CoverageRecord, Evidence, Finding, FindingStatus
from services.control_plane.app.risk_service import (
    cvss_base_score,
    derive_cvss_vector,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    action_ref TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    artifact_refs TEXT NOT NULL,
    replay_proof TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    confidence REAL NOT NULL,
    redacted INTEGER NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    target_ref TEXT NOT NULL,
    vuln_category TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    param TEXT NOT NULL,
    status TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    evidence_ids TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retest_proof TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_fingerprint ON findings(fingerprint);
CREATE TABLE IF NOT EXISTS coverage (
    target_ref TEXT PRIMARY KEY,
    observed TEXT NOT NULL,
    known TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
"""


class EvidenceStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._ensure_column("findings", "run_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("findings", "severity", "TEXT NOT NULL DEFAULT 'medium'")
        self._ensure_column("findings", "asset_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("findings", "remediation", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("findings", "cvss_vector", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("findings", "cvss_score", "REAL NOT NULL DEFAULT 0")
        self._backfill_cvss()
        self._conn.commit()

    def _execute(self, sql: str, params=()):
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return SqliteResult(cursor.fetchall(), cursor.rowcount)

    def _backfill_cvss(self) -> None:
        rows = self._execute(
            "SELECT finding_id, vuln_category, severity FROM findings "
            "WHERE cvss_vector = ''"
        ).fetchall()
        for row in rows:
            vector = derive_cvss_vector(
                str(row["vuln_category"]),
                str(row["severity"] or "medium"),
            )
            self._execute(
                "UPDATE findings SET cvss_vector = ?, cvss_score = ? "
                "WHERE finding_id = ?",
                (
                    vector,
                    cvss_base_score(vector),
                    row["finding_id"],
                ),
            )

    def close(self) -> None:
        self._conn.close()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self._execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self._execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def save_evidence(self, evidence: Evidence) -> Evidence:
        evidence = evidence.model_copy(update={"hash": evidence.compute_hash()})
        with self._lock, self._conn:
            self._execute(
                """
                INSERT OR REPLACE INTO evidence
                    (evidence_id, source_type, target_ref, occurred_at, action_ref,
                     tool_version, artifact_refs, replay_proof, parser_version,
                     confidence, redacted, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.source_type,
                    evidence.target_ref,
                    evidence.occurred_at,
                    evidence.action_ref,
                    evidence.tool_version,
                    json.dumps(evidence.artifact_refs),
                    json.dumps(evidence.replay_proof, ensure_ascii=True),
                    evidence.parser_version,
                    evidence.confidence,
                    int(evidence.redacted),
                    evidence.hash,
                ),
            )
        return evidence

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        row = self._execute(
            "SELECT * FROM evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        return self._evidence(row) if row is not None else None

    def verify_evidence(self, evidence_id: str) -> bool:
        evidence = self.get_evidence(evidence_id)
        if evidence is None:
            return False
        return evidence.compute_hash() == evidence.hash

    def save_finding(self, finding: Finding) -> Finding:
        finding = finding.model_copy(
            update={"cvss_score": cvss_base_score(finding.cvss_vector)}
        )
        with self._lock, self._conn:
            self._execute(
                """
                INSERT OR REPLACE INTO findings
                    (finding_id, run_id, target_ref, vuln_category, endpoint, param, status,
                     fingerprint, evidence_ids, notes, created_at, updated_at,
                     retest_proof, severity, asset_id, remediation, cvss_vector, cvss_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding.finding_id,
                    finding.run_id,
                    finding.target_ref,
                    finding.vuln_category,
                    finding.endpoint,
                    finding.param,
                    finding.status.value,
                    finding.fingerprint,
                    json.dumps(finding.evidence_ids),
                    finding.notes,
                    finding.created_at,
                    finding.updated_at,
                    json.dumps(finding.retest_proof, ensure_ascii=True),
                    finding.severity,
                    finding.asset_id,
                    finding.remediation,
                    finding.cvss_vector,
                    finding.cvss_score,
                ),
            )
        return finding

    def get_finding(self, finding_id: str) -> Finding | None:
        row = self._execute(
            "SELECT * FROM findings WHERE finding_id = ?",
            (finding_id,),
        ).fetchone()
        return self._finding(row) if row is not None else None

    def find_by_fingerprint(self, fingerprint: str) -> Finding | None:
        row = self._execute(
            "SELECT * FROM findings WHERE fingerprint = ? LIMIT 1",
            (fingerprint,),
        ).fetchone()
        return self._finding(row) if row is not None else None

    def findings_by_fingerprint(self, fingerprint: str) -> list[Finding]:
        rows = self._execute(
            "SELECT * FROM findings WHERE fingerprint = ? ORDER BY created_at",
            (fingerprint,),
        ).fetchall()
        return [self._finding(row) for row in rows]

    def list_findings(self) -> list[Finding]:
        rows = self._execute(
            "SELECT * FROM findings ORDER BY created_at"
        ).fetchall()
        return [self._finding(row) for row in rows]

    def list_evidence(self) -> list[Evidence]:
        rows = self._execute(
            "SELECT * FROM evidence ORDER BY evidence_id"
        ).fetchall()
        return [self._evidence(row) for row in rows]

    def list_findings_by_run(self, run_id: str) -> list[Finding]:
        rows = self._execute(
            "SELECT * FROM findings WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [self._finding(row) for row in rows]

    def update_status(self, finding_id: str, status: FindingStatus) -> Finding:
        with self._lock, self._conn:
            self._execute(
                "UPDATE findings SET status = ?, updated_at = ? WHERE finding_id = ?",
                (status.value, _now(), finding_id),
            )
        finding = self.get_finding(finding_id)
        if finding is None:
            raise KeyError(finding_id)
        return finding

    def update_finding_metadata(
        self,
        finding_id: str,
        *,
        severity: str | None = None,
        asset_id: str | None = None,
        remediation: str | None = None,
        notes: str | None = None,
        cvss_vector: str | None = None,
    ) -> Finding:
        updates: list[str] = []
        params: list[object] = []
        if severity is not None:
            updates.append("severity = ?")
            params.append(severity)
        if asset_id is not None:
            updates.append("asset_id = ?")
            params.append(asset_id)
        if remediation is not None:
            updates.append("remediation = ?")
            params.append(remediation)
        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)
        if cvss_vector is not None:
            updates.append("cvss_vector = ?")
            params.append(cvss_vector)
            updates.append("cvss_score = ?")
            params.append(cvss_base_score(cvss_vector))
        if not updates:
            return self.get_finding(finding_id) or self._raise(finding_id)
        updates.append("updated_at = ?")
        params.append(_now())
        params.append(finding_id)
        with self._lock, self._conn:
            self._execute(
                f"UPDATE findings SET {', '.join(updates)} WHERE finding_id = ?",
                params,
            )
        finding = self.get_finding(finding_id)
        if finding is None:
            raise KeyError(finding_id)
        return finding

    def _raise(self, finding_id: str) -> Finding:
        raise KeyError(finding_id)

    def save_coverage(self, record: CoverageRecord) -> CoverageRecord:
        with self._lock, self._conn:
            self._execute(
                """
                INSERT OR REPLACE INTO coverage
                    (target_ref, observed, known, observed_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    record.target_ref,
                    json.dumps(record.observed),
                    json.dumps(record.known),
                    record.observed_at,
                ),
            )
        return record

    def get_coverage(self, target_ref: str) -> CoverageRecord | None:
        row = self._execute(
            "SELECT * FROM coverage WHERE target_ref = ?",
            (target_ref,),
        ).fetchone()
        return self._coverage(row) if row is not None else None

    def list_coverage(self) -> list[CoverageRecord]:
        rows = self._execute(
            "SELECT * FROM coverage ORDER BY target_ref"
        ).fetchall()
        return [self._coverage(row) for row in rows]

    def _evidence(self, row: sqlite3.Row) -> Evidence:
        data = dict(row)
        data["artifact_refs"] = json.loads(data["artifact_refs"])
        data["replay_proof"] = json.loads(data["replay_proof"])
        data["redacted"] = bool(data["redacted"])
        return Evidence(**data)

    def _finding(self, row: sqlite3.Row) -> Finding:
        data = dict(row)
        data["status"] = FindingStatus(data["status"])
        data["evidence_ids"] = json.loads(data["evidence_ids"])
        data["retest_proof"] = json.loads(data["retest_proof"])
        return Finding(**data)

    def _coverage(self, row: sqlite3.Row) -> CoverageRecord:
        data = dict(row)
        data["observed"] = json.loads(data["observed"])
        data["known"] = json.loads(data["known"])
        return CoverageRecord(**data)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
