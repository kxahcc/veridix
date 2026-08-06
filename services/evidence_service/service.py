from __future__ import annotations

from uuid import uuid4

from dataclasses import dataclass, field

from .evidence_store import EvidenceStore
from .fingerprint import finding_fingerprint
from .models import CoverageRecord, Evidence, Finding, FindingStatus
from services.control_plane.app.risk_service import derive_cvss_vector


@dataclass(frozen=True)
class MergedFinding:
    fingerprint: str
    target_ref: str
    vuln_category: str
    endpoint: str
    status: FindingStatus
    primary_finding_id: str
    evidence_ids: tuple[str, ...] = ()
    source_finding_ids: tuple[str, ...] = ()

    @property
    def duplicate_count(self) -> int:
        return max(0, len(self.source_finding_ids) - 1)

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "target_ref": self.target_ref,
            "vuln_category": self.vuln_category,
            "endpoint": self.endpoint,
            "status": self.status.value,
            "primary_finding_id": self.primary_finding_id,
            "evidence_ids": list(self.evidence_ids),
            "source_finding_ids": list(self.source_finding_ids),
            "duplicate_count": self.duplicate_count,
        }


class EvidenceService:
    def __init__(self, store: EvidenceStore) -> None:
        self._store = store

    def submit_candidate(
        self,
        *,
        run_id: str = "",
        target_ref: str,
        vuln_category: str,
        endpoint: str,
        param: str = "",
        evidence: Evidence | None = None,
        notes: str = "",
        severity: str | None = None,
    ) -> Finding:
        fingerprint = finding_fingerprint(
            target_ref=target_ref,
            vuln_category=vuln_category,
            endpoint=endpoint,
            param=param,
        )
        if evidence is not None:
            self._store.save_evidence(evidence)
        duplicate = self._store.find_by_fingerprint(fingerprint)
        if evidence is not None:
            evidence_ids = [evidence.evidence_id]
        elif duplicate is not None:
            evidence_ids = list(duplicate.evidence_ids)
        else:
            evidence_ids = []
        finding = Finding(
            finding_id=f"finding_{uuid4().hex[:12]}",
            run_id=run_id,
            target_ref=target_ref,
            vuln_category=vuln_category,
            endpoint=endpoint,
            param=param,
            status=FindingStatus.DUPLICATE if duplicate is not None else FindingStatus.CANDIDATE,
            fingerprint=fingerprint,
            evidence_ids=evidence_ids,
            notes=notes,
            severity=severity or "medium",
            cvss_vector=derive_cvss_vector(
                vuln_category,
                severity or "medium",
            ),
        )
        return self._store.save_finding(finding)

    def support(self, finding_id: str) -> Finding:
        finding = self._require(finding_id)
        if finding.status not in (FindingStatus.CANDIDATE, FindingStatus.SUPPORTED):
            raise ValueError(f"cannot support finding in state {finding.status.value}")
        if not all(
            self._store.verify_evidence(evidence_id)
            for evidence_id in finding.evidence_ids
        ):
            raise ValueError("evidence hash verification failed")
        return self._store.update_status(finding_id, FindingStatus.SUPPORTED)

    def verify(self, finding_id: str, *, oracle: str) -> Finding:
        finding = self._require(finding_id)
        if oracle == "verified":
            return self._store.update_status(finding_id, FindingStatus.VERIFIED)
        if oracle == "not_verified":
            return self._store.update_status(finding_id, FindingStatus.REJECTED)
        return self._store.update_status(finding_id, FindingStatus.INCONCLUSIVE)

    def retest(self, finding_id: str, *, proof: dict) -> Finding:
        finding = self._require(finding_id)
        if finding.status not in (
            FindingStatus.VERIFIED,
            FindingStatus.OPEN,
            FindingStatus.FIXED,
        ):
            raise ValueError(f"cannot retest finding in state {finding.status.value}")
        finding = finding.model_copy(update={"retest_proof": proof})
        self._store.save_finding(finding)
        if proof.get("matched") is True:
            return self._store.update_status(finding_id, FindingStatus.RETEST_PASSED)
        return self._store.update_status(finding_id, FindingStatus.OPEN)

    def review(
        self,
        finding_id: str,
        *,
        decision: str,
        decided_by: str,
    ) -> Finding:
        decisions = {
            "open": FindingStatus.OPEN,
            "rejected": FindingStatus.REJECTED,
            "accepted_risk": FindingStatus.ACCEPTED_RISK,
            "reviewed": FindingStatus.REVIEWED,
        }
        if decision not in decisions:
            raise ValueError(f"unknown review decision {decision}")
        finding = self._require(finding_id)
        if finding.status not in (FindingStatus.SUPPORTED, FindingStatus.VERIFIED):
            raise ValueError(
                f"cannot review finding in state {finding.status.value}"
            )
        note = f"reviewed_by={decided_by}, decision={decision}"
        updated = finding.model_copy(
            update={
                "notes": (
                    f"{finding.notes}\n{note}" if finding.notes else note
                )
            }
        )
        self._store.save_finding(updated)
        return self._store.update_status(finding_id, decisions[decision])

    def record_negative_evidence(
        self,
        *,
        target_ref: str,
        endpoint: str,
        action_ref: str = "",
        replay_proof: dict | None = None,
    ) -> Evidence:
        evidence = Evidence(
            evidence_id=f"ev_negative_{uuid4().hex[:8]}",
            source_type="negative",
            target_ref=target_ref,
            action_ref=action_ref,
            replay_proof=replay_proof or {},
            confidence=0.0,
        )
        return self._store.save_evidence(evidence)

    def save_coverage(
        self,
        *,
        target_ref: str,
        observed: list[str],
        known: list[str],
    ) -> CoverageRecord:
        record = CoverageRecord(
            target_ref=target_ref,
            observed=observed,
            known=known,
        )
        return self._store.save_coverage(record)

    def _require(self, finding_id: str) -> Finding:
        finding = self._store.get_finding(finding_id)
        if finding is None:
            raise KeyError(finding_id)
        return finding

    def list_findings_by_run(self, run_id: str) -> list[Finding]:
        return self._store.list_findings_by_run(run_id)

    def list_all_findings(self) -> list[Finding]:
        return self._store.list_findings()

    def get_finding(self, finding_id: str) -> Finding | None:
        return self._store.get_finding(finding_id)

    def update_metadata(
        self,
        finding_id: str,
        *,
        severity: str | None = None,
        asset_id: str | None = None,
        remediation: str | None = None,
        notes: str | None = None,
        cvss_vector: str | None = None,
    ) -> Finding:
        return self._store.update_finding_metadata(
            finding_id,
            severity=severity,
            asset_id=asset_id,
            remediation=remediation,
            notes=notes,
            cvss_vector=cvss_vector,
        )

    def append_note(self, finding_id: str, note: str) -> Finding:
        finding = self._require(finding_id)
        notes = f"{finding.notes}\n{note}" if finding.notes else note
        return self.update_metadata(finding_id, notes=notes)

    def evidence_map(self) -> dict[str, Evidence]:
        return {
            evidence.evidence_id: evidence
            for evidence in self._store.list_evidence()
        }

    def merged_views(self, *, run_id: str | None = None) -> list[MergedFinding]:
        findings = (
            self._store.list_findings_by_run(run_id)
            if run_id is not None
            else self._store.list_findings()
        )
        groups: dict[str, list[Finding]] = {}
        for finding in findings:
            if finding.fingerprint:
                groups.setdefault(finding.fingerprint, []).append(finding)
        views: list[MergedFinding] = []
        for fingerprint, group in groups.items():
            primary = group[-1]
            evidence_ids: list[str] = []
            for finding in group:
                for evidence_id in finding.evidence_ids:
                    if evidence_id not in evidence_ids:
                        evidence_ids.append(evidence_id)
            views.append(
                MergedFinding(
                    fingerprint=fingerprint,
                    target_ref=primary.target_ref,
                    vuln_category=primary.vuln_category,
                    endpoint=primary.endpoint,
                    status=primary.status,
                    primary_finding_id=primary.finding_id,
                    evidence_ids=tuple(evidence_ids),
                    source_finding_ids=tuple(finding.finding_id for finding in group),
                )
            )
        return sorted(views, key=lambda view: view.fingerprint)
