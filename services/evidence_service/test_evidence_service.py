from __future__ import annotations

import threading

import pytest

from services.evidence_service.evidence_store import EvidenceStore
from services.evidence_service.models import Evidence, FindingStatus
from services.evidence_service.report import (
    export_benchmark,
    export_html,
    export_json,
    export_junit,
    export_markdown,
    export_sarif,
)
from services.evidence_service.service import EvidenceService


def make_evidence(evidence_id: str, target: str = "https://lab.example.test") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type="web.replay",
        target_ref=target,
        action_ref="proxy.replay",
        tool_version="wp08-1",
        artifact_refs=[f"artifact://{evidence_id}/raw"],
        replay_proof={
            "baseline_status": 200,
            "mutated_status": 200,
            "matched": True,
        },
        confidence=0.8,
    )


def test_finding_state_machine_covers_all_exit_fixtures() -> None:
    service = EvidenceService(EvidenceStore(":memory:"))

    verified = service.submit_candidate(
        target_ref="https://lab.example.test",
        vuln_category="authz",
        endpoint="/admin",
        param="role",
        evidence=make_evidence("ev_verified"),
    )
    service.support(verified.finding_id)
    verified = service.verify(verified.finding_id, oracle="verified")
    assert verified.status == FindingStatus.VERIFIED

    duplicate = service.submit_candidate(
        target_ref="https://lab.example.test",
        vuln_category="authz",
        endpoint="/admin",
        param="role",
        evidence=make_evidence("ev_duplicate"),
    )
    assert duplicate.status == FindingStatus.DUPLICATE
    assert verified.evidence_ids == ["ev_verified"]

    inconclusive = service.submit_candidate(
        target_ref="https://lab.example.test",
        vuln_category="xss",
        endpoint="/search",
        param="q",
        evidence=make_evidence("ev_inconclusive"),
    )
    inconclusive = service.verify(inconclusive.finding_id, oracle="inconclusive")
    assert inconclusive.status == FindingStatus.INCONCLUSIVE

    retested = service.retest(
        verified.finding_id,
        proof={"matched": True, "replayed_status": 200},
    )
    assert retested.status == FindingStatus.RETEST_PASSED


def test_evidence_hash_verification_detects_tampering() -> None:
    store = EvidenceStore(":memory:")
    service = EvidenceService(store)
    finding = service.submit_candidate(
        target_ref="https://lab.example.test",
        vuln_category="authz",
        endpoint="/admin",
        evidence=make_evidence("ev_hash"),
    )
    evidence = store.get_evidence("ev_hash")
    assert evidence is not None
    assert store.verify_evidence(evidence.evidence_id) is True

    store._conn.execute(
        "UPDATE evidence SET hash = ? WHERE evidence_id = ?",
        ("deadbeef", evidence.evidence_id),
    )
    store._conn.commit()

    assert store.verify_evidence(evidence.evidence_id) is False
    with pytest.raises(ValueError, match="hash verification failed"):
        service.support(finding.finding_id)


def test_evidence_store_concurrent_reads_are_thread_safe() -> None:
    store = EvidenceStore(":memory:")
    service = EvidenceService(store)
    for index in range(20):
        service.submit_candidate(
            run_id=f"run_{index % 2}",
            target_ref="https://lab.example.test",
            vuln_category="authz",
            endpoint=f"/admin/{index}",
            evidence=make_evidence(f"ev_concurrent_{index}"),
        )

    errors: list[Exception] = []

    def reader(run_id: str) -> None:
        try:
            for _ in range(50):
                assert len(store.list_findings_by_run(run_id)) == 10
        except Exception as error:  # pragma: no cover - failure path
            errors.append(error)

    threads = [
        threading.Thread(target=reader, args=(f"run_{index % 2}",))
        for index in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors


def test_report_export_json_markdown_sarif() -> None:
    store = EvidenceStore(":memory:")
    service = EvidenceService(store)
    evidence = make_evidence("ev_report")
    finding = service.submit_candidate(
        target_ref="https://lab.example.test",
        vuln_category="authz",
        endpoint="/admin",
        param="role",
        evidence=evidence,
    )
    evidence_map = {evidence.evidence_id: evidence}

    payload = export_json([finding], evidence_map)
    markdown = export_markdown([finding], evidence_map)
    sarif = export_sarif([finding], evidence_map)

    assert payload["findings"][0]["finding_id"] == finding.finding_id
    assert "authz" in markdown
    assert "Findings: 1" in markdown
    assert "source=web.replay" in markdown
    assert "artifacts=artifact://ev_report/raw" in markdown
    assert "Remediation: Enforce server-side authorization" in markdown
    html = export_html([finding], evidence_map)
    assert "<html" in html
    assert "Findings:" in html
    assert "authz" in html
    assert "replay matched" in html
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"][0]["ruleId"] == "authz"


def test_report_export_junit_and_benchmark() -> None:
    store = EvidenceStore(":memory:")
    service = EvidenceService(store)
    evidence = make_evidence("ev_junit")
    finding = service.submit_candidate(
        target_ref="https://lab.example.test",
        vuln_category="authz",
        endpoint="/admin",
        param="role",
        evidence=evidence,
    )
    service.support(finding.finding_id)
    finding = service.verify(finding.finding_id, oracle="verified")
    evidence_map = {evidence.evidence_id: evidence}

    junit = export_junit([finding], evidence_map)
    benchmark = export_benchmark([finding], evidence_map)

    assert 'testsuite name="veridix"' in junit
    assert 'failures="1"' in junit
    assert 'name="authz"' in junit
    assert benchmark["total_findings"] == 1
    assert benchmark["summary"]["verified"] == 1
    assert benchmark["evidence_count"] == 1


def test_review_command_and_negative_evidence_and_coverage() -> None:
    service = EvidenceService(EvidenceStore(":memory:"))
    finding = service.submit_candidate(
        target_ref="https://lab.example.test",
        vuln_category="authz",
        endpoint="/admin",
        evidence=make_evidence("ev_review"),
    )
    service.support(finding.finding_id)
    finding = service.verify(finding.finding_id, oracle="verified")

    reviewed = service.review(
        finding.finding_id,
        decision="open",
        decided_by="operator",
    )
    assert reviewed.status == FindingStatus.OPEN
    assert "reviewed_by=operator" in reviewed.notes

    negative = service.record_negative_evidence(
        target_ref="https://lab.example.test",
        endpoint="/health",
        action_ref="proxy.replay",
        replay_proof={"matched": False},
    )
    assert negative.source_type == "negative"
    assert negative.confidence == 0.0

    coverage = service.save_coverage(
        target_ref="https://lab.example.test",
        observed=["/", "/admin"],
        known=["/", "/admin", "/api/health"],
    )
    assert coverage.ratio == pytest.approx(2 / 3, abs=0.001)
    stored = service._store.get_coverage("https://lab.example.test")
    assert stored is not None
    assert stored.observed == ["/", "/admin"]


def test_duplicate_merge_view_keeps_all_evidence() -> None:
    service = EvidenceService(EvidenceStore(":memory:"))
    first = service.submit_candidate(
        run_id="run_1",
        target_ref="https://lab.example.test",
        vuln_category="authz",
        endpoint="/admin",
        param="role",
        evidence=make_evidence("ev_first"),
    )
    second = service.submit_candidate(
        run_id="run_1",
        target_ref="https://lab.example.test",
        vuln_category="authz",
        endpoint="/admin",
        param="role",
        evidence=make_evidence("ev_second"),
    )

    views = service.merged_views(run_id="run_1")

    assert first.status == FindingStatus.CANDIDATE
    assert second.status == FindingStatus.DUPLICATE
    assert len(views) == 1
    assert views[0].evidence_ids == ("ev_first", "ev_second")
    assert views[0].duplicate_count == 1
    assert views[0].source_finding_ids == (
        first.finding_id,
        second.finding_id,
    )
