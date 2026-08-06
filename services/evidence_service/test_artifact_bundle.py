from __future__ import annotations

import io
import zipfile

from services.control_plane.app.artifact_store import ArtifactStore
from services.evidence_service.artifact_bundle import (
    build_artifact_bundle_bytes,
    bundle_manifest,
)
from services.evidence_service.models import Evidence, Finding, FindingStatus


def test_artifact_bundle_includes_reports_and_artifacts(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    artifact = store.put(b"artifact-bytes", content_type="text/plain")
    evidence = Evidence(
        evidence_id="ev_bundle",
        source_type="web.replay",
        target_ref="https://lab.example.test",
        action_ref="proxy.replay",
        artifact_refs=[f"artifact://sha256/{artifact.artifact_id}"],
        replay_proof={"matched": True},
        confidence=0.8,
    )
    finding = Finding(
        finding_id="finding_bundle",
        run_id="run_1",
        target_ref="https://lab.example.test",
        vuln_category="authz",
        endpoint="/admin",
        status=FindingStatus.VERIFIED,
        evidence_ids=["ev_bundle"],
    )

    data = build_artifact_bundle_bytes(
        findings=[finding],
        evidence={evidence.evidence_id: evidence},
        artifact_store=store,
    )
    manifest = bundle_manifest(data)

    assert "report.json" in manifest["reports"]
    assert "report.sarif" in manifest["reports"]
    assert "report.junit.xml" in manifest["reports"]
    assert manifest["artifacts"] == [f"artifacts/{artifact.artifact_id}"]
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        assert bundle.read(f"artifacts/{artifact.artifact_id}") == b"artifact-bytes"
