from __future__ import annotations

from services.control_plane.app.risk_service import (
    cvss_base_score,
    derive_cvss_vector,
)
from services.evidence_service.evidence_store import EvidenceStore
from services.evidence_service.service import EvidenceService


def test_derive_cvss_vector_uses_category_first() -> None:
    vector = derive_cvss_vector("SQLi", "low")
    assert vector == "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:N"
    assert cvss_base_score(vector) == 8.1


def test_derive_cvss_vector_handles_exposure() -> None:
    vector = derive_cvss_vector("Exposure")
    assert vector == "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:N/A:N"
    assert 4.0 <= cvss_base_score(vector) <= 5.0


def test_derive_cvss_vector_falls_back_to_severity() -> None:
    vector = derive_cvss_vector("unknown-category", "high")
    assert vector == "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H"
    assert cvss_base_score(vector) == 8.8


def test_finding_creation_auto_scores_cvss() -> None:
    service = EvidenceService(EvidenceStore(":memory:"))
    finding = service.submit_candidate(
        target_ref="http://lab.example.test",
        vuln_category="SQLi",
        endpoint="/sqli",
    )
    assert finding.cvss_vector.startswith("CVSS:3.1/")
    assert finding.cvss_score == 8.1


def test_evidence_store_backfills_legacy_empty_vectors(tmp_path) -> None:
    from services.evidence_service.models import Finding

    db = tmp_path / "legacy.db"
    store = EvidenceStore(db)
    store.save_finding(
        Finding(
            finding_id="finding_legacy",
            target_ref="http://lab.example.test",
            vuln_category="Exposure",
            endpoint="/admin",
            severity="medium",
        )
    )
    store.close()
    recreated = EvidenceStore(db)
    row = recreated._execute(
        "SELECT cvss_vector, cvss_score FROM findings "
        "WHERE finding_id = 'finding_legacy'"
    ).fetchone()
    assert row["cvss_vector"].startswith("CVSS:3.1/")
    assert row["cvss_score"] > 0
