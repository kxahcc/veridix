from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FindingStatus(str, Enum):
    CANDIDATE = "candidate"
    SUPPORTED = "supported"
    VERIFIED = "verified"
    REVIEWED = "reviewed"
    OPEN = "open"
    FIXED = "fixed"
    RETEST_PASSED = "retest_passed"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    INCONCLUSIVE = "inconclusive"
    ACCEPTED_RISK = "accepted_risk"


from pydantic import BaseModel, Field


def canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class Evidence(BaseModel):
    evidence_id: str
    source_type: str
    target_ref: str
    occurred_at: str = Field(default_factory=utc_now)
    action_ref: str = ""
    tool_version: str = ""
    artifact_refs: list[str] = Field(default_factory=list)
    replay_proof: dict[str, Any] = Field(default_factory=dict)
    parser_version: str = "1"
    confidence: float = 0.5
    redacted: bool = False
    hash: str = ""

    def compute_hash(self) -> str:
        return canonical_hash(
            {
                "evidence_id": self.evidence_id,
                "source_type": self.source_type,
                "target_ref": self.target_ref,
                "occurred_at": self.occurred_at,
                "action_ref": self.action_ref,
                "tool_version": self.tool_version,
                "artifact_refs": self.artifact_refs,
                "replay_proof": self.replay_proof,
                "parser_version": self.parser_version,
                "confidence": self.confidence,
                "redacted": self.redacted,
            }
        )


class Finding(BaseModel):
    finding_id: str
    run_id: str = ""
    target_ref: str
    vuln_category: str
    endpoint: str
    param: str = ""
    status: FindingStatus = FindingStatus.CANDIDATE
    severity: str = "medium"
    asset_id: str = ""
    remediation: str = ""
    cvss_vector: str = ""
    cvss_score: float = 0.0
    fingerprint: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    notes: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    retest_proof: dict[str, Any] = Field(default_factory=dict)


class CoverageRecord(BaseModel):
    target_ref: str
    observed: list[str] = Field(default_factory=list)
    known: list[str] = Field(default_factory=list)
    observed_at: str = Field(default_factory=utc_now)

    @property
    def ratio(self) -> float:
        if not self.known:
            return 1.0
        return round(len(set(self.observed) & set(self.known)) / len(self.known), 3)
