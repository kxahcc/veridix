from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from services.control_plane.app.artifact_store import ArtifactStore

from .models import Evidence, Finding
from .report import export_json, export_junit, export_markdown, export_sarif


def build_artifact_bundle_bytes(
    *,
    findings: list[Finding],
    evidence: dict[str, Evidence],
    artifact_store: ArtifactStore,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "report.json",
            json.dumps(export_json(findings, evidence), indent=2, ensure_ascii=True),
        )
        bundle.writestr("report.md", export_markdown(findings, evidence))
        bundle.writestr(
            "report.sarif",
            json.dumps(export_sarif(findings, evidence), indent=2, ensure_ascii=True),
        )
        bundle.writestr("report.junit.xml", export_junit(findings, evidence))
        for finding in findings:
            for evidence_id in finding.evidence_ids:
                record = evidence.get(evidence_id)
                if record is None:
                    continue
                for artifact_ref in record.artifact_refs:
                    artifact_id = artifact_ref.rsplit("/", 1)[-1]
                    if artifact_store.verify(artifact_id):
                        bundle.writestr(
                            f"artifacts/{artifact_id}",
                            artifact_store.get(artifact_id),
                        )
    return buffer.getvalue()


def bundle_manifest(data: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        names = bundle.namelist()
    return {
        "reports": [name for name in names if name.startswith("report.")],
        "artifacts": [name for name in names if name.startswith("artifacts/")],
        "file_count": len(names),
    }
