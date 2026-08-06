from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal
from urllib.parse import urlparse

from .contracts import AgentEvent, utc_now
from .domain import ApprovalRequest, LeaseRecord, Mission, Project, RunState, TargetProfile
from .provider_probe import list_provider_models, probe_provider
from .run_service import DomainError
from .auth import require_api_token
from .risk_service import summarize as risk_summarize
from .role_templates import list_builtin_templates
from .provider_presets import list_provider_presets
from .mcp_presets import list_mcp_presets
from .asset_store import ASSET_LIFECYCLE
from services.agent_runtime.kernel.loop_profiles import REGISTRY
from services.agent_runtime.kernel.loop_presets import REGISTRY as LOOP_PRESET_REGISTRY
from services.evidence_service.models import Evidence, Finding
from services.evidence_service.artifact_bundle import build_artifact_bundle_bytes
from services.evidence_service.report import export_html, export_markdown
from services.knowledge_service.retrieval_config import (
    probe_retrieval_config,
    resolve_retrieval_config,
)
from services.knowledge_service.models import parse_skill_markdown

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_token)])


def _request_role(request: Request) -> str:
    identity = getattr(request.state, "identity", None) or {}
    return str(identity.get("role") or "admin")


def _identity_projects(request: Request) -> set[str] | None:
    identity = getattr(request.state, "identity", None) or {}
    projects = identity.get("projects") or ()
    if not projects:
        return None
    return {str(item) for item in projects}


def _require_role(request: Request, *allowed: str) -> None:
    role = _request_role(request)
    if role not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"role {role} is not allowed for this action",
        )


def _audit(
    request: Request,
    action: str,
    resource: str,
    detail: str = "",
) -> None:
    store = getattr(request.app.state, "audit", None)
    if store is None:
        return
    store.record(
        actor=_request_role(request),
        action=action,
        resource=resource,
        detail=detail,
        ip=request.client.host if request.client else "",
    )


def _memory_fact_payload(fact) -> dict:
    return {
        "fact_id": fact.fact_id,
        "subject": fact.subject,
        "predicate": fact.predicate,
        "value": fact.value,
        "target": fact.target,
        "source_refs": list(fact.source_refs),
        "confidence": fact.confidence,
        "trust": fact.trust,
        "observed_at": fact.observed_at,
        "expires_at": fact.expires_at,
        "metadata": dict(fact.metadata or {}),
    }


def _memory_view_payload(view) -> dict:
    payload = _memory_fact_payload(view.fact)
    payload["status"] = view.status
    return payload


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    owner: str = Field(default="local", min_length=1)


class CreateTargetRequest(BaseModel):
    url: str = Field(min_length=1)
    allowed: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    authorization: str = "authorized"


class CreateMissionRequest(BaseModel):
    project_id: str
    name: str = Field(min_length=1)
    spec: dict = Field(default_factory=dict)


class StartRunRequest(BaseModel):
    mission_id: str
    idempotency_key: str


class RunCommandRequest(BaseModel):
    idempotency_key: str
    reason: str | None = None


class RunMessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1)
    operator: str = "web-operator"


class TakeoverRunIn(BaseModel):
    idempotency_key: str
    taken_by: str
    reason: str = ""


class ClaimRunIn(BaseModel):
    worker_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_.-]+$")
    idempotency_key: str = Field(min_length=1)


class FinishRunIn(BaseModel):
    outcome: Literal["succeeded", "failed"]
    idempotency_key: str = Field(min_length=1)
    stop_reason: str = ""
    summary: str = ""


class ApprovalRequestIn(BaseModel):
    tool_ref: str = Field(min_length=1)
    risk_level: str = Field(min_length=1)
    idempotency_key: str
    reason: str = ""


class ApprovalDecisionIn(BaseModel):
    approved: bool
    decided_by: str = Field(min_length=1)
    reason: str = ""


class LeaseHeartbeatIn(BaseModel):
    lease_seconds: int = Field(default=30, ge=5, le=3600)


class ProviderProbeIn(BaseModel):
    provider_id: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_ref: str | None = None
    backend: str = "openai"
    litellm_provider: str = ""
    timeout_seconds: int = Field(default=5, ge=1, le=60)
    run_id: str | None = None


class IngestResourceEventIn(BaseModel):
    event_id: str = Field(min_length=1)
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    actor: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    payload: dict = Field(default_factory=dict)


class WebObservationsIn(BaseModel):
    observations: list[dict]


class EvidenceIn(BaseModel):
    source_type: str = "external_scanner"
    artifact_refs: list[str] = []
    replay_proof: dict = {}
    confidence: float = 0.5
    occurred_at: str = ""
    action_ref: str = ""
    tool_version: str = ""
    parser_version: str = "1"
    redacted: bool = False


class KnowledgeIn(BaseModel):
    chunk_id: str
    source_ref: str
    content: str
    project_id: str = ""
    trust: str = "project_trusted"
    version: str = "1"
    subjects: list[str] = []
    target_refs: list[str] = []
    observed_at: str = ""
    expires_at: str | None = None


class KnowledgeImportIn(BaseModel):
    source_id: str
    content: str
    license: str = "unknown"
    version: str = "1"
    project_id: str = ""
    subjects: list[str] = []
    target_refs: list[str] = []


class MemoryFixIn(BaseModel):
    project_id: str = "default"
    subject: str
    predicate: str
    value: str
    reason: str = "human_fix"


class MemoryForgetIn(BaseModel):
    project_id: str = "default"
    reason: str = "human_forget"


class MemoryClearIn(BaseModel):
    project_id: str = "default"
    reason: str = "memory_cleared"


class MemoryRecordIn(BaseModel):
    project_id: str = "default"
    subject: str
    predicate: str
    value: str
    target: str = ""
    source_refs: list[str] = []
    confidence: float = 0.8
    trust: str = "user_approved"
    expires_in_seconds: int | None = None
    metadata: dict = Field(default_factory=dict)


class SubmitFindingIn(BaseModel):
    target_ref: str
    vuln_category: str
    endpoint: str
    param: str = ""
    severity: str | None = Field(default=None, max_length=24)
    notes: str = ""
    evidence: EvidenceIn | None = None


class FindingDecisionIn(BaseModel):
    decision: str
    decided_by: str


class VerifyFindingIn(BaseModel):
    oracle: str


class RetestFindingIn(BaseModel):
    proof: dict


class FindingNoteIn(BaseModel):
    note: str = Field(min_length=1)


class RemoteNodeRegisterIn(BaseModel):
    node_id: str = Field(min_length=1)
    version: str = Field(default="0.1.0")
    capabilities: list[str] = Field(default_factory=list)
    public_key: str = Field(default="")


class RemoteNodeHeartbeatIn(BaseModel):
    lease_seconds: int = Field(default=300, ge=30, le=86400)


class RemoteNodeResultIn(BaseModel):
    task_ref: str = Field(min_length=1)
    status: str = Field(default="completed")
    artifact_refs: list[str] = Field(default_factory=list)
    signature: str = Field(default="")
    payload: dict = Field(default_factory=dict)


class RemoteNodeLeaseIn(BaseModel):
    task_ref: str = Field(min_length=1)
    lease_seconds: int = Field(default=300, ge=30, le=86400)


class RemoteNodeDispatchIn(BaseModel):
    task_ref: str = Field(min_length=1)
    payload: dict = Field(default_factory=dict)
    lease_seconds: int = Field(default=300, ge=30, le=86400)


class HumanGateResolveIn(BaseModel):
    approved: bool
    reason: str = ""


class RegisterRunnerIn(BaseModel):
    runner_id: str
    kind: str
    status: str = "online"


class RegisterProviderIn(BaseModel):
    provider_id: str
    model: str
    endpoint: str
    status: str = "ok"
    api_key_ref: str = ""
    backend: str = "openai"
    litellm_provider: str = ""
    timeout_seconds: float | None = None
    thinking_mode: str | None = None
    reasoning_effort: str | None = None
    retries: int | None = None
    streaming: bool | None = None
    max_tokens: int | None = None
    headers: dict = Field(default_factory=dict)


class ProviderDefaultIn(BaseModel):
    provider_id: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_ref: str = ""


class AssetIn(BaseModel):
    project_id: str = Field(min_length=1)
    kind: str = "url"
    value: str = Field(min_length=1)
    source: str = "manual"
    status: str = "known"
    metadata: dict = Field(default_factory=dict)


class AssetUpdateIn(BaseModel):
    status: str | None = None
    metadata: dict | None = None


class SessionUpdateIn(BaseModel):
    title: str | None = None
    archived: bool | None = None


class VulnerabilityUpdateIn(BaseModel):
    severity: str | None = Field(default=None, max_length=24)
    asset_id: str | None = None
    remediation: str | None = None
    notes: str | None = None
    cvss_vector: str | None = Field(default=None, max_length=128)


class RoleTemplateIn(BaseModel):
    template_id: str = Field(min_length=1)
    label: str = ""
    description: str = ""
    roles: list[dict] = Field(default_factory=list)


def _project_id_for_run(request: Request, run_id: str) -> str:
    run = request.app.state.control.get_run(run_id)
    mission = request.app.state.control.get_mission(run.mission_id)
    return mission.project_id


def _require_project_access(request: Request, project_id: str) -> None:
    scope = _identity_projects(request)
    if scope is not None and project_id not in scope:
        raise HTTPException(
            status_code=403,
            detail=f"not allowed for project {project_id}",
        )


def _require_mission_access(request: Request, mission_id: str) -> Mission:
    try:
        mission = request.app.state.control.get_mission(mission_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _require_project_access(request, mission.project_id)
    return mission


def _require_run_access(request: Request, run_id: str) -> None:
    try:
        project_id = _project_id_for_run(request, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _require_project_access(request, project_id)


def _require_target_access(request: Request, target_id: str) -> TargetProfile:
    try:
        target = request.app.state.control.get_target(target_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _require_project_access(request, target.project_id)
    return target


def _require_asset_access(request: Request, asset_id: str) -> dict:
    asset = request.app.state.assets.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"asset {asset_id} not found")
    _require_project_access(request, str(asset["project_id"]))
    return asset


def _require_session_access(request: Request, session_id: str) -> dict:
    session = request.app.state.sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    _require_project_access(request, str(session["project_id"]))
    return session


def _require_finding_access(request: Request, finding_id: str) -> Finding:
    finding = request.app.state.evidence.get_finding(finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail=f"finding {finding_id} not found")
    if finding.run_id:
        _require_run_access(request, finding.run_id)
    return finding


def _upsert_assets_for_url(
    request: Request,
    project_id: str,
    url: str,
) -> None:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return
    assets = request.app.state.assets
    assets.upsert(
        project_id=project_id,
        kind="url",
        value=url,
        source="observation",
    )
    host = parsed.hostname or ""
    if not host:
        return
    import re

    is_ip = re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host) is not None
    assets.upsert(
        project_id=project_id,
        kind="ip" if is_ip else "host",
        value=host,
        source="observation",
        metadata={"port": parsed.port},
    )
    if not is_ip:
        labels = host.split(".")
        if len(labels) >= 2:
            assets.upsert(
                project_id=project_id,
                kind="domain",
                value=".".join(labels[-2:]),
                source="observation",
            )


class RegisterToolIn(BaseModel):
    tool_ref: str
    capability: str
    status: str = "available"


class RegisterSkillIn(BaseModel):
    skill_ref: str
    name: str
    version: str
    status: str = "available"
    trigger: str = ""
    runner: str = ""
    risk_level: str = "L1"


class RegisterMcpIn(BaseModel):
    server_id: str
    name: str
    status: str = "available"
    kind: str = "local"
    command: str = ""
    description: str = ""
    env: dict = Field(default_factory=dict)
    timeout_seconds: float | None = None


class BatchImportIn(BaseModel):
    items: list[dict] = Field(default_factory=list)


@router.post("/projects", response_model=Project)
def create_project(body: CreateProjectRequest, request: Request) -> Project:
    _require_role(request, "admin")
    project = request.app.state.control.create_project(
        body.name,
        owner=body.owner,
    )
    _audit(request, "project.create", project.project_id, body.name)
    return project


@router.get("/projects", response_model=list[Project])
def list_projects(request: Request) -> list[Project]:
    rows = request.app.state.control.list_projects()
    scope = _identity_projects(request)
    if scope is not None:
        rows = [row for row in rows if row.project_id in scope]
    return rows


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, request: Request) -> dict:
    _require_role(request, "admin")
    try:
        deleted_runs = request.app.state.control.delete_project(project_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    for run_id in deleted_runs:
        request.app.state.web_observations.delete_run(run_id)
    _audit(request, "project.delete", project_id, f"runs={len(deleted_runs)}")
    return {"deleted": True, "project_id": project_id, "runs": deleted_runs}


@router.post("/projects/{project_id}/targets", response_model=TargetProfile)
def create_target(project_id: str, body: CreateTargetRequest, request: Request) -> TargetProfile:
    _require_project_access(request, project_id)
    try:
        target = request.app.state.control.create_target(
            project_id,
            body.url,
            allowed=tuple(body.allowed),
            excluded=tuple(body.excluded),
            authorization=body.authorization,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    request.app.state.assets.upsert(
        project_id=project_id,
        kind="url",
        value=body.url,
        source="target",
    )
    return target


@router.post("/missions", response_model=Mission)
def create_mission(body: CreateMissionRequest, request: Request) -> Mission:
    _require_project_access(request, body.project_id)
    try:
        return request.app.state.control.create_mission(
            body.project_id,
            body.name,
            body.spec,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/missions", response_model=list[Mission])
def list_missions(request: Request) -> list[Mission]:
    rows = request.app.state.control.list_all_missions()
    scope = _identity_projects(request)
    if scope is not None:
        rows = [row for row in rows if row.project_id in scope]
    return rows


@router.get("/missions/{mission_id}", response_model=Mission)
def get_mission(mission_id: str, request: Request) -> Mission:
    return _require_mission_access(request, mission_id)


@router.get("/targets/{target_id}", response_model=TargetProfile)
def get_target(target_id: str, request: Request) -> TargetProfile:
    return _require_target_access(request, target_id)


@router.get("/assets")
def list_assets(request: Request, project_id: str | None = None) -> list[dict]:
    rows = request.app.state.assets.list(project_id)
    scope = _identity_projects(request)
    if scope is not None:
        rows = [row for row in rows if row["project_id"] in scope]
    findings = request.app.state.evidence.list_all_findings()
    counts: dict[str, int] = {}
    for finding in findings:
        if finding.asset_id:
            counts[finding.asset_id] = counts.get(finding.asset_id, 0) + 1
            continue
        for asset in rows:
            value = str(asset["value"])
            if value and (
                value in finding.endpoint or finding.endpoint.startswith(value)
            ):
                counts[asset["asset_id"]] = counts.get(asset["asset_id"], 0) + 1
                break
    for asset in rows:
        asset["finding_count"] = counts.get(asset["asset_id"], 0)
    return rows


@router.get("/assets/lifecycle")
def asset_lifecycle() -> dict:
    return {"lifecycle": list(ASSET_LIFECYCLE)}


@router.post("/assets")
def create_asset(body: AssetIn, request: Request) -> dict:
    _require_project_access(request, body.project_id)
    return request.app.state.assets.upsert(
        project_id=body.project_id,
        kind=body.kind,
        value=body.value,
        source=body.source,
        status=body.status,
        metadata=body.metadata,
    )


@router.post("/projects/{project_id}/assets/import")
def import_project_targets(project_id: str, request: Request) -> dict:
    _require_project_access(request, project_id)
    try:
        targets = request.app.state.control.list_targets(project_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    imported = 0
    for target in targets:
        request.app.state.assets.upsert(
            project_id=project_id,
            kind="url",
            value=target.url,
            source="target",
        )
        imported += 1
    return {"imported": imported, "project_id": project_id}


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, request: Request) -> dict:
    return _require_asset_access(request, asset_id)


@router.patch("/assets/{asset_id}")
def update_asset(asset_id: str, body: AssetUpdateIn, request: Request) -> dict:
    _require_asset_access(request, asset_id)
    try:
        result = request.app.state.assets.update(
            asset_id,
            status=body.status,
            metadata=body.metadata,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _audit(
        request,
        "asset.update",
        asset_id,
        f"status={body.status or ''}",
    )
    return result


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, request: Request) -> dict:
    _require_asset_access(request, asset_id)
    deleted = request.app.state.assets.delete(asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"asset {asset_id} not found")
    return {"deleted": True, "asset_id": asset_id}


@router.get("/assets/{asset_id}/findings")
def asset_findings(asset_id: str, request: Request) -> list[dict]:
    asset = _require_asset_access(request, asset_id)
    findings = request.app.state.evidence.list_all_findings()
    value = str(asset["value"])
    return [
        finding.model_dump()
        for finding in findings
        if finding.asset_id == asset_id
        or (value and (value in finding.endpoint or finding.endpoint.startswith(value)))
    ]


@router.get("/sessions")
def list_sessions(
    request: Request,
    project_id: str | None = None,
    archived: bool | None = False,
) -> list[dict]:
    rows = request.app.state.sessions.list(project_id=project_id, archived=archived)
    scope = _identity_projects(request)
    if scope is not None:
        rows = [row for row in rows if row["project_id"] in scope]
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            run = request.app.state.control.get_run(row["run_id"])
            item["status"] = run.status
            item["event_count"] = run.event_count
        except KeyError:
            item["status"] = "orphan"
            item["event_count"] = 0
        result.append(item)
    return result


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: SessionUpdateIn, request: Request) -> dict:
    _require_session_access(request, session_id)
    try:
        return request.app.state.sessions.update(
            session_id,
            title=body.title,
            archived=body.archived,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, request: Request) -> dict:
    _require_session_access(request, session_id)
    deleted = request.app.state.sessions.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return {"deleted": True, "session_id": session_id}


@router.get("/vulnerabilities")
def list_vulnerabilities(
    request: Request,
    project_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
) -> list[dict]:
    findings = request.app.state.evidence.list_all_findings()
    result: list[dict] = []
    scope = _identity_projects(request)
    for finding in findings:
        view = finding.model_dump()
        project = ""
        try:
            run = request.app.state.control.get_run(finding.run_id)
            mission = request.app.state.control.get_mission(run.mission_id)
            project = mission.project_id
        except Exception:
            pass
        view["project_id"] = project
        if project_id and project != project_id:
            continue
        if scope is not None and project not in scope:
            continue
        if status and finding.status.value != status:
            continue
        if severity and finding.severity != severity:
            continue
        result.append(view)
    return result


@router.patch("/vulnerabilities/{finding_id}")
def update_vulnerability(
    finding_id: str,
    body: VulnerabilityUpdateIn,
    request: Request,
) -> Finding:
    _require_finding_access(request, finding_id)
    try:
        result = request.app.state.evidence.update_metadata(
            finding_id,
            severity=body.severity,
            asset_id=body.asset_id,
            remediation=body.remediation,
            notes=body.notes,
            cvss_vector=body.cvss_vector,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    _audit(
        request,
        "vulnerability.update",
        finding_id,
        f"severity={body.severity or ''} cvss={body.cvss_vector or ''}",
    )
    return result


@router.get("/risk")
def risk_summary(request: Request, project_id: str | None = None) -> dict:
    findings = request.app.state.evidence.list_all_findings()
    if project_id:
        filtered = []
        for finding in findings:
            try:
                run = request.app.state.control.get_run(finding.run_id)
                mission = request.app.state.control.get_mission(run.mission_id)
            except Exception:
                continue
            if mission.project_id == project_id:
                filtered.append(finding)
        findings = filtered
    assets = request.app.state.assets.list(project_id)
    return risk_summarize(findings, assets)


@router.get("/runtime/role-templates")
def list_role_templates(request: Request) -> list[dict]:
    custom = request.app.state.registry.list_role_templates()
    return [*list_builtin_templates(), *custom]


@router.get("/runtime/role-templates/{template_id}")
def get_role_template(template_id: str, request: Request) -> dict:
    for item in list_builtin_templates():
        if item["template_id"] == template_id:
            return item
    custom = request.app.state.registry.get_role_template(template_id)
    if custom is None:
        raise HTTPException(status_code=404, detail=f"template {template_id} not found")
    return custom


@router.post("/runtime/role-templates")
def save_role_template(body: RoleTemplateIn, request: Request) -> dict:
    payload = body.model_dump()
    return request.app.state.registry.save_role_template(body.template_id, payload)


@router.post("/runtime/role-templates/import")
def import_role_templates(body: BatchImportIn, request: Request) -> dict:
    imported = 0
    for item in body.items:
        template_id = str(item.get("template_id") or "")
        if not template_id:
            continue
        request.app.state.registry.save_role_template(
            template_id,
            {
                **item,
                "template_id": template_id,
            },
        )
        imported += 1
    _audit(request, "role_template.import", "*", f"count={imported}")
    return {"imported": imported}


@router.delete("/runtime/role-templates/{template_id}")
def delete_role_template(template_id: str, request: Request) -> dict:
    if any(item["template_id"] == template_id for item in list_builtin_templates()):
        raise HTTPException(status_code=400, detail="cannot delete builtin template")
    deleted = request.app.state.registry.delete_role_template(template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"template {template_id} not found")
    return {"deleted": True, "template_id": template_id}


@router.get("/runtime/loop-profiles")
def list_loop_profiles() -> dict:
    return REGISTRY.as_dict()


@router.get("/runtime/loop-presets")
def list_loop_presets() -> dict:
    return LOOP_PRESET_REGISTRY.as_dict()


@router.post("/missions/{mission_id}/runs", response_model=RunState)
def start_run(mission_id: str, body: StartRunRequest, request: Request) -> RunState:
    mission = _require_mission_access(request, mission_id)
    try:
        run = request.app.state.runs.start_run(mission_id, body.idempotency_key)
        request.app.state.sessions.upsert_for_run(
            run_id=run.run_id,
            project_id=mission.project_id,
            title=mission.name,
            last_message=str(mission.spec.get("mission") or mission.name),
        )
        _audit(
            request,
            "run.start",
            run.run_id,
            f"mission={mission_id}",
        )
        return run
    except (KeyError, DomainError) as error:
        code = error.code if isinstance(error, DomainError) else "not_found"
        raise HTTPException(status_code=404 if code == "mission_not_found" else 400, detail=str(error)) from error


@router.get("/runs/{run_id}", response_model=RunState)
def get_run(run_id: str, request: Request) -> RunState:
    _require_run_access(request, run_id)
    try:
        return request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/runs", response_model=list[RunState])
def list_runs(
    request: Request,
    project_id: str | None = Query(default=None),
) -> list[RunState]:
    runs = request.app.state.control.list_all_runs()
    if project_id:
        runs = [
            run
            for run in runs
            if _project_id_for_run(request, run.run_id) == project_id
        ]
    scope = _identity_projects(request)
    if scope is not None:
        runs = [
            run
            for run in runs
            if _project_id_for_run(request, run.run_id) in scope
        ]
    return runs


@router.get("/reports/summary")
def reports_summary(
    request: Request,
    project_id: str | None = Query(default=None),
) -> dict:
    runs = request.app.state.control.list_all_runs()
    if project_id:
        runs = [
            run
            for run in runs
            if _project_id_for_run(request, run.run_id) == project_id
        ]
    scope = _identity_projects(request)
    if scope is not None:
        runs = [
            run
            for run in runs
            if _project_id_for_run(request, run.run_id) in scope
        ]
    evidence = request.app.state.evidence
    evidence_map = evidence.evidence_map()
    all_findings = evidence.list_all_findings()
    rows: list[dict] = []
    for run in runs:
        try:
            findings = evidence.list_findings_by_run(run.run_id)
        except Exception:
            findings = []
        gate = _evidence_gate_summary(
            evidence,
            run.run_id,
            evidence_map=evidence_map,
            all_findings=all_findings,
        )
        rows.append(
            {
                "run_id": run.run_id,
                "mission_id": run.mission_id,
                "status": run.status,
                "created_at": run.created_at,
                "findings": len(findings),
                "verified": gate["verified"],
                "gate_pass": gate["gate_pass"],
                "sources": _finding_sources(findings),
            }
        )
    return {"rows": rows, "total": len(rows)}


@router.post("/runs/{run_id}/pause", response_model=RunState)
def pause_run(run_id: str, body: RunCommandRequest, request: Request) -> RunState:
    return _run_command(request, run_id, body, "pause")


@router.post("/runs/{run_id}/resume", response_model=RunState)
def resume_run(run_id: str, body: RunCommandRequest, request: Request) -> RunState:
    return _run_command(request, run_id, body, "resume")


@router.post("/runs/{run_id}/cancel", response_model=RunState)
def cancel_run(run_id: str, body: RunCommandRequest, request: Request) -> RunState:
    return _run_command(request, run_id, body, "cancel")


@router.post("/runs/{run_id}/message", response_model=RunState)
def send_run_message(run_id: str, body: RunMessageIn, request: Request) -> RunState:
    _require_run_access(request, run_id)
    try:
        run = request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if run.status != "paused":
        raise HTTPException(
            status_code=409,
            detail="run must be paused before accepting a message",
        )
    event = AgentEvent(
        event_id=f"{run_id}:user.message:{uuid4().hex[:8]}",
        event_type="user.message",
        stream_id=run_id,
        run_id=run_id,
        actor=body.operator,
        occurred_at=utc_now(),
        payload={"message": body.message, "operator": body.operator},
    )
    request.app.state.events.append(event)
    request.app.state.sessions.touch(run_id, last_message=body.message)
    try:
        return request.app.state.runs.resume(run_id, body.idempotency_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DomainError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/runs/{run_id}/fork", response_model=RunState)
def fork_run(run_id: str, body: RunCommandRequest, request: Request) -> RunState:
    _require_run_access(request, run_id)
    try:
        return request.app.state.runs.fork_run(run_id, body.idempotency_key)
    except (KeyError, DomainError) as error:
        code = error.code if isinstance(error, DomainError) else "not_found"
        status = 404 if code == "not_found" else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post("/runs/{run_id}/takeover", response_model=RunState)
def takeover_run(run_id: str, body: TakeoverRunIn, request: Request) -> RunState:
    _require_run_access(request, run_id)
    try:
        return request.app.state.runs.takeover(
            run_id,
            body.idempotency_key,
            taken_by=body.taken_by,
            reason=body.reason,
        )
    except (KeyError, DomainError) as error:
        code = error.code if isinstance(error, DomainError) else "not_found"
        status = 404 if code == "not_found" else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post("/runs/{run_id}/claim", response_model=RunState)
def claim_run(run_id: str, body: ClaimRunIn, request: Request) -> RunState:
    _require_run_access(request, run_id)
    try:
        return request.app.state.runs.claim(
            run_id,
            body.worker_id,
            body.idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DomainError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/runs/{run_id}/finish", response_model=RunState)
def finish_run(run_id: str, body: FinishRunIn, request: Request) -> RunState:
    _require_run_access(request, run_id)
    try:
        return request.app.state.runs.finish(
            run_id,
            body.outcome,
            body.idempotency_key,
            stop_reason=body.stop_reason,
            summary=body.summary,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DomainError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/runs/{run_id}/events", response_model=list[AgentEvent])
def run_events(
    run_id: str,
    request: Request,
    after: int = 0,
) -> list[AgentEvent]:
    _require_run_access(request, run_id)
    try:
        return request.app.state.events.replay(run_id, after=after)
    except Exception as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


async def event_stream_generator(
    events,
    run_id: str,
    after: int = 0,
):
    """SSE framing generator; cursor advances as events are emitted."""
    cursor = after
    while True:
        batch = events.replay(run_id, after=cursor)
        for event in batch:
            yield f"data: {event.model_dump_json()}\n\n"
            cursor = event.sequence or cursor
        await asyncio.sleep(0.2)


@router.get("/runs/{run_id}/events/stream")
async def stream_run_events(
    run_id: str,
    request: Request,
    after: int = 0,
) -> StreamingResponse:
    _require_run_access(request, run_id)
    events = request.app.state.events

    async def generator():
        async for item in event_stream_generator(events, run_id, after):
            yield item
            if await request.is_disconnected():
                break

    return StreamingResponse(generator(), media_type="text/event-stream")


@router.post("/runs/{run_id}/approvals", response_model=ApprovalRequest)
def request_approval(
    run_id: str,
    body: ApprovalRequestIn,
    request: Request,
) -> ApprovalRequest:
    _require_run_access(request, run_id)
    try:
        return request.app.state.runs.request_approval(
            run_id,
            body.tool_ref,
            body.risk_level,
            body.idempotency_key,
            reason=body.reason,
        )
    except (KeyError, DomainError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/runs/{run_id}/events", response_model=AgentEvent)
def ingest_resource_event(
    run_id: str,
    body: IngestResourceEventIn,
    request: Request,
) -> AgentEvent:
    _require_run_access(request, run_id)
    try:
        event = request.app.state.runs.ingest_resource_event(
            run_id,
            event_id=body.event_id,
            event_type=body.event_type,
            actor=body.actor,
            payload=body.payload,
        )
        if body.event_type in ("user.message", "run.submitted"):
            last_message = str(
                body.payload.get("message")
                or body.payload.get("user_input")
                or ""
            )
            request.app.state.sessions.touch(run_id, last_message=last_message)
        return event
    except DomainError as error:
        status = 404 if error.code == "run_not_found" else 403
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post("/runs/{run_id}/web-observations")
def upsert_web_observations(
    run_id: str,
    body: WebObservationsIn,
    request: Request,
) -> dict:
    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    store = request.app.state.web_observations
    for observation in body.observations:
        if "request_id" not in observation:
            raise HTTPException(status_code=400, detail="request_id is required")
        store.upsert(run_id, observation)
        try:
            project_id = _project_id_for_run(request, run_id)
            _upsert_assets_for_url(
                request,
                project_id,
                str(observation.get("url") or observation.get("endpoint") or ""),
            )
        except Exception:
            pass
    return {"stored": len(body.observations)}


@router.get("/runs/{run_id}/web-observations")
def list_web_observations(run_id: str, request: Request) -> list[dict]:
    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return request.app.state.web_observations.list(run_id)


@router.get("/runs/{run_id}/findings", response_model=list[Finding])
def list_findings(run_id: str, request: Request) -> list[Finding]:
    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return request.app.state.evidence.list_findings_by_run(run_id)


@router.get("/runs/{run_id}/evidence")
def run_evidence(run_id: str, request: Request) -> list[dict]:
    _require_run_access(request, run_id)
    findings = request.app.state.evidence.list_findings_by_run(run_id)
    wanted = {
        evidence_id
        for finding in findings
        for evidence_id in finding.evidence_ids
    }
    evidence_map = request.app.state.evidence.evidence_map()
    return [
        evidence_map[evidence_id].model_dump()
        for evidence_id in sorted(wanted)
        if evidence_id in evidence_map
    ]


@router.get("/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: str,
    request: Request,
    preview: bool = False,
    max_bytes: int = 1_000_000,
) -> Response:
    try:
        data = request.app.state.artifact_store.get(artifact_id)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if preview:
        text = data.decode("utf-8", errors="replace")
        truncated = len(text) > max_bytes
        return Response(
            content=json.dumps(
                {
                    "artifact_id": artifact_id,
                    "truncated": truncated,
                    "preview": text[:max_bytes],
                },
                ensure_ascii=True,
            ),
            media_type="application/json",
        )
    return Response(
        content=data,
        media_type="application/octet-stream",
    )


@router.post("/knowledge")
def add_knowledge(body: KnowledgeIn, request: Request) -> dict:
    from services.knowledge_service.models import KnowledgeChunk, utc_now

    _require_project_access(request, body.project_id)
    chunk = request.app.state.knowledge.add_chunk(
        KnowledgeChunk(
            chunk_id=body.chunk_id,
            source_ref=body.source_ref,
            content=body.content,
            project_id=body.project_id,
            trust=body.trust,
            version=body.version,
            subjects=tuple(body.subjects),
            target_refs=tuple(body.target_refs),
            observed_at=(
                body.observed_at
                if body.observed_at
                else utc_now()
            ),
            expires_at=body.expires_at,
        )
    )
    meta = request.app.state.knowledge.list_meta().get(
        chunk.chunk_id,
        {},
    )
    request.app.state.events.append(
        AgentEvent(
            event_id=f"knowledge:{chunk.chunk_id}:{uuid4().hex[:8]}",
            event_type="knowledge.added",
            stream_id="knowledge",
            run_id="",
            actor="api/control",
            payload={
                "chunk_id": chunk.chunk_id,
                "project_id": chunk.project_id,
            },
        )
    )
    return {
        "chunk_id": chunk.chunk_id,
        "source_ref": chunk.source_ref,
        "project_id": chunk.project_id,
        "trust": chunk.trust,
        "revision": meta.get("revision", 1),
        "updated_at": meta.get("updated_at", ""),
    }


@router.post("/knowledge/import")
def import_knowledge(body: KnowledgeImportIn, request: Request) -> dict:
    from services.knowledge_service.import_pipeline import ImportPipeline

    _require_project_access(request, body.project_id)
    pipeline = ImportPipeline(
        request.app.state.knowledge,
        registry=request.app.state.knowledge_sources,
    )
    report = pipeline.import_markdown(
        body.content,
        source_id=body.source_id,
        license=body.license,
        version=body.version,
        project_id=body.project_id,
        subjects=tuple(body.subjects),
        target_refs=tuple(body.target_refs),
    )
    request.app.state.events.append(
        AgentEvent(
            event_id=f"knowledge:import:{body.source_id}:{uuid4().hex[:8]}",
            event_type="knowledge.imported",
            stream_id="knowledge",
            run_id="",
            actor="api/control",
            payload={
                "source_id": report.source_id,
                "chunk_count": report.chunk_count,
                "content_hash": report.content_hash,
                "license": report.license,
                "version": report.version,
            },
        )
    )
    return {
        "source_id": report.source_id,
        "chunk_count": report.chunk_count,
        "content_hash": report.content_hash,
        "skipped_existing": report.skipped_existing,
        "license": report.license,
        "version": report.version,
    }


@router.post("/knowledge/import-file")
async def import_knowledge_file(
    request: Request,
    file: UploadFile = File(...),
    source_id: str = Form(...),
    license: str = Form("unknown"),
    version: str = Form("1"),
    project_id: str = Form(""),
) -> dict:
    from services.knowledge_service.import_pipeline import ImportPipeline

    _require_project_access(request, project_id)
    content = await file.read()
    pipeline = ImportPipeline(
        request.app.state.knowledge,
        registry=request.app.state.knowledge_sources,
        graph_store=request.app.state.knowledge_graph,
    )
    report = pipeline.import_document_bytes(
        content,
        filename=file.filename or "upload.txt",
        source_id=source_id,
        license=license,
        version=version,
        project_id=project_id,
    )
    request.app.state.events.append(
        AgentEvent(
            event_id=f"knowledge:import-file:{source_id}:{uuid4().hex[:8]}",
            event_type="knowledge.imported",
            stream_id="knowledge",
            run_id="",
            actor="api/control",
            payload={
                "source_id": report.source_id,
                "chunk_count": report.chunk_count,
                "content_hash": report.content_hash,
                "license": report.license,
                "version": report.version,
                "filename": file.filename or "",
            },
        )
    )
    return {
        "source_id": report.source_id,
        "chunk_count": report.chunk_count,
        "content_hash": report.content_hash,
        "skipped_existing": report.skipped_existing,
        "license": report.license,
        "version": report.version,
        "filename": file.filename or "",
    }


@router.get("/knowledge/sources")
def list_knowledge_sources(request: Request) -> list[dict]:
    return [
        {
            "source_id": source.source_id,
            "name": source.name,
            "kind": source.kind,
            "location": source.location,
            "license": source.license,
            "version": source.version,
            "content_hash": source.content_hash,
            "status": source.status,
            "imported_at": source.imported_at,
        }
        for source in request.app.state.knowledge_sources.list()
    ]


@router.put("/knowledge/{chunk_id}")
def update_knowledge(
    chunk_id: str,
    body: KnowledgeIn,
    request: Request,
) -> dict:
    from services.knowledge_service.models import KnowledgeChunk, utc_now

    existing = request.app.state.knowledge.get_chunk(chunk_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"knowledge chunk {chunk_id} not found",
        )
    _require_project_access(request, existing.project_id or body.project_id)
    updated = request.app.state.knowledge.update_chunk(
        KnowledgeChunk(
            chunk_id=chunk_id,
            source_ref=body.source_ref,
            content=body.content,
            project_id=body.project_id,
            trust=body.trust,
            version=body.version,
            subjects=tuple(body.subjects),
            target_refs=tuple(body.target_refs),
            observed_at=(
                body.observed_at
                if body.observed_at
                else utc_now()
            ),
            expires_at=body.expires_at,
        )
    )
    if not updated:
        raise HTTPException(
            status_code=404,
            detail=f"knowledge chunk {chunk_id} not found",
        )
    meta = request.app.state.knowledge.list_meta().get(chunk_id, {})
    request.app.state.events.append(
        AgentEvent(
            event_id=f"knowledge:{chunk_id}:{uuid4().hex[:8]}",
            event_type="knowledge.updated",
            stream_id="knowledge",
            run_id="",
            actor="api/control",
            payload={
                "chunk_id": chunk_id,
                "project_id": body.project_id,
                "revision": meta.get("revision", 1),
            },
        )
    )
    return {
        "updated": chunk_id,
        "revision": meta.get("revision", 1),
        "updated_at": meta.get("updated_at", ""),
    }


@router.get("/knowledge")
def list_knowledge(
    request: Request,
    project_id: str = "",
) -> list[dict]:
    meta = request.app.state.knowledge.list_meta()
    rows = [
        {
            "chunk_id": chunk.chunk_id,
            "source_ref": chunk.source_ref,
            "project_id": chunk.project_id,
            "trust": chunk.trust,
            "subjects": list(chunk.subjects),
            "target_refs": list(chunk.target_refs),
            "content": chunk.content,
            "version": chunk.version,
            "observed_at": chunk.observed_at,
            "expires_at": chunk.expires_at,
            "revision": meta.get(chunk.chunk_id, {}).get("revision", 1),
            "updated_at": meta.get(chunk.chunk_id, {}).get(
                "updated_at",
                "",
            ),
        }
        for chunk in request.app.state.knowledge.list_chunks(
            project_id=project_id or None,
        )
    ]
    scope = _identity_projects(request)
    if scope is not None:
        rows = [
            row
            for row in rows
            if row["project_id"] in scope or not row["project_id"]
        ]
    return rows


@router.get("/knowledge/events")
def knowledge_events(
    request: Request,
    limit: int = 50,
    chunk_id: str = "",
) -> dict:
    events = request.app.state.events.replay("knowledge")
    if chunk_id:
        events = [
            event
            for event in events
            if event.payload.get("chunk_id") == chunk_id
        ]
    total = len(events)
    if limit > 0:
        events = events[-limit:]
    return {
        "total": total,
        "events": [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "payload": event.payload,
            }
            for event in events
        ],
    }


@router.get("/knowledge/graph")
def knowledge_graph(request: Request, limit: int = 200) -> dict:
    store = getattr(request.app.state, "knowledge_graph", None)
    if store is None:
        return {
            "nodes": [],
            "edges": [],
            "counts": {"nodes": 0, "edges": 0, "chunks": 0},
        }
    return store.snapshot(node_limit=limit)


@router.delete("/knowledge/{chunk_id}")
def delete_knowledge(chunk_id: str, request: Request) -> dict:
    chunk = request.app.state.knowledge.get_chunk(chunk_id)
    if chunk is None:
        raise HTTPException(
            status_code=404,
            detail=f"knowledge chunk {chunk_id} not found",
        )
    _require_project_access(request, chunk.project_id)
    removed = request.app.state.knowledge.delete_chunk(chunk_id)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"knowledge chunk {chunk_id} not found",
        )
    request.app.state.events.append(
        AgentEvent(
            event_id=f"knowledge:{chunk_id}:{uuid4().hex[:8]}",
            event_type="knowledge.deleted",
            stream_id="knowledge",
            run_id="",
            actor="api/control",
            payload={"chunk_id": chunk_id},
        )
    )
    return {"deleted": chunk_id}


@router.get("/knowledge/search")
def search_knowledge(
    request: Request,
    q: str,
    limit: int = 10,
    project_id: str = "",
    target_ref: str = "",
    observed_since: str = "",
    observed_until: str = "",
) -> list[dict]:
    chunks, excluded = request.app.state.knowledge.search(
        q,
        limit=limit,
        project_id=project_id or None,
        target_ref=target_ref or None,
        observed_since=observed_since or None,
        observed_until=observed_until or None,
    )
    scope = _identity_projects(request)
    if scope is not None:
        chunks = [
            chunk
            for chunk in chunks
            if chunk.project_id in scope or not chunk.project_id
        ]
    return [
        {
            "chunk_id": chunk.chunk_id,
            "source_ref": chunk.source_ref,
            "project_id": chunk.project_id,
            "content": chunk.content,
            "target_refs": list(chunk.target_refs),
            "observed_at": chunk.observed_at,
        }
        for chunk in chunks
    ] + [{"excluded": excluded}]


@router.get("/memory")
def list_memory(
    request: Request,
    project_id: str = "default",
    subject: str = "",
    include_stale: bool = False,
    limit: int = 100,
) -> dict:
    _require_project_access(request, project_id or "default")
    memory = request.app.state.memory.get(project_id or "default")
    views = memory.projection(subject=subject or None)
    if not include_stale:
        views = [
            view
            for view in views
            if view.status in ("active", "conflict")
        ]
    views = views[: max(1, min(500, limit))]
    snapshot = memory.snapshot()
    return {
        "project_id": memory.project_id,
        "snapshot": {
            "total_facts": snapshot.total_facts,
            "active": snapshot.active,
            "conflict": snapshot.conflict,
            "stale": snapshot.stale,
        },
        "facts": [_memory_view_payload(view) for view in views],
        "summaries": list(memory.summaries(limit=10)),
    }


@router.post("/memory/fix")
def fix_memory(body: MemoryFixIn, request: Request) -> dict:
    _require_project_access(request, body.project_id or "default")
    memory = request.app.state.memory.get(
        body.project_id or "default"
    )
    fact = memory.fix(
        body.subject,
        body.predicate,
        body.value,
        reason=body.reason,
    )
    request.app.state.events.append(
        AgentEvent(
            event_id=f"memory:fix:{uuid4().hex[:8]}",
            event_type="memory.user.fixed",
            stream_id="memory",
            run_id="",
            actor="api/control",
            payload={
                "fact_id": fact.fact_id,
                "subject": fact.subject,
                "predicate": fact.predicate,
            },
        )
    )
    _audit(request, "memory.fix", fact.fact_id, body.reason)
    return _memory_fact_payload(fact)


@router.post("/memory/record")
def record_memory(body: MemoryRecordIn, request: Request) -> dict:
    allowed_trust = {
        "user_approved",
        "project_trusted",
        "project_observed",
    }
    if body.trust not in allowed_trust:
        raise HTTPException(
            status_code=400,
            detail=(
                f"memory.record trust must be one of "
                f"{', '.join(sorted(allowed_trust))}"
            ),
        )
    expires_at: str | None = None
    if body.expires_in_seconds is not None:
        if body.expires_in_seconds <= 0:
            raise HTTPException(
                status_code=400,
                detail="expires_in_seconds must be positive",
            )
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=body.expires_in_seconds)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    _require_project_access(request, body.project_id or "default")
    memory = request.app.state.memory.get(
        body.project_id or "default"
    )
    fact, inserted = memory.record(
        body.subject,
        body.predicate,
        body.value,
        target=body.target,
        source_refs=tuple(body.source_refs),
        confidence=body.confidence,
        trust=body.trust,
        expires_at=expires_at,
        metadata=dict(body.metadata or {}),
    )
    request.app.state.events.append(
        AgentEvent(
            event_id=f"memory:record:{uuid4().hex[:8]}",
            event_type="memory.user.recorded",
            stream_id="memory",
            run_id="",
            actor="api/control",
            payload={
                "fact_id": fact.fact_id,
                "subject": fact.subject,
                "predicate": fact.predicate,
                "inserted": inserted,
            },
        )
    )
    _audit(request, "memory.record", fact.fact_id, body.predicate)
    payload = _memory_fact_payload(fact)
    payload["inserted"] = inserted
    return payload


@router.post("/memory/clear")
def clear_memory(body: MemoryClearIn, request: Request) -> dict:
    _require_project_access(request, body.project_id or "default")
    memory = request.app.state.memory.get(
        body.project_id or "default"
    )
    cleared = memory.clear(reason=body.reason)
    request.app.state.events.append(
        AgentEvent(
            event_id=f"memory:clear:{uuid4().hex[:8]}",
            event_type="memory.user.cleared",
            stream_id="memory",
            run_id="",
            actor="api/control",
            payload={"cleared": cleared, "reason": body.reason},
        )
    )
    _audit(request, "memory.clear", "project/default", body.reason)
    return {"cleared": cleared}


@router.post("/memory/{fact_id}/forget")
def forget_memory(
    fact_id: str,
    body: MemoryForgetIn,
    request: Request,
) -> dict:
    _require_project_access(request, body.project_id or "default")
    memory = request.app.state.memory.get(
        body.project_id or "default"
    )
    try:
        fact = memory.forget(fact_id, reason=body.reason)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"memory fact {fact_id} not found",
        )
    request.app.state.events.append(
        AgentEvent(
            event_id=f"memory:forget:{uuid4().hex[:8]}",
            event_type="memory.user.forgotten",
            stream_id="memory",
            run_id="",
            actor="api/control",
            payload={"fact_id": fact_id, "reason": body.reason},
        )
    )
    _audit(request, "memory.forget", fact_id, body.reason)
    return {"forgotten": fact_id, "fact_id": fact.fact_id}


@router.get("/runs/{run_id}/findings/merged")
def list_merged_findings(run_id: str, request: Request) -> list[dict]:
    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return [
        view.to_dict()
        for view in request.app.state.evidence.merged_views(run_id=run_id)
    ]


@router.get("/runs/{run_id}/report-bundle")
def report_bundle(run_id: str, request: Request) -> Response:
    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    findings = request.app.state.evidence.list_findings_by_run(run_id)
    data = build_artifact_bundle_bytes(
        findings=findings,
        evidence=request.app.state.evidence.evidence_map(),
        artifact_store=request.app.state.artifact_store,
    )
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="report-{run_id}.zip"'
        },
    )


@router.get("/runs/{run_id}/report")
def report_markdown(run_id: str, request: Request) -> Response:
    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    findings = request.app.state.evidence.list_findings_by_run(run_id)
    markdown = export_markdown(
        findings,
        request.app.state.evidence.evidence_map(),
    )
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/runs/{run_id}/report.html")
def report_html(run_id: str, request: Request) -> Response:
    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    findings = request.app.state.evidence.list_findings_by_run(run_id)
    html = export_html(
        findings,
        request.app.state.evidence.evidence_map(),
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
    )


def _remote_node_dict(node) -> dict:
    return {
        "node_id": node.node_id,
        "version": node.version,
        "capabilities": list(node.capabilities),
        "public_key": node.public_key,
        "status": node.status,
        "last_seen_at": node.last_seen_at,
        "created_at": node.created_at,
    }


def _remote_result_dict(result) -> dict:
    return {
        "result_id": result.result_id,
        "node_id": result.node_id,
        "task_ref": result.task_ref,
        "status": result.status,
        "artifact_refs": list(result.artifact_refs),
        "signature": result.signature,
        "payload": result.payload,
    }


@router.get("/remote/nodes")
def list_remote_nodes(request: Request) -> list[dict]:
    registry = getattr(request.app.state, "remote_nodes", None)
    if registry is None:
        return []
    registry.reconcile_connections()
    return [_remote_node_dict(node) for node in registry.list()]


@router.post("/remote/nodes")
def register_remote_node(
    body: RemoteNodeRegisterIn,
    request: Request,
) -> dict:
    from runners.remote.models import NodeRegistration

    registry = request.app.state.remote_nodes
    node = registry.register(
        NodeRegistration(
            node_id=body.node_id,
            version=body.version,
            capabilities=tuple(body.capabilities),
            public_key=body.public_key,
        )
    )
    return _remote_node_dict(node)


@router.post("/remote/nodes/{node_id}/heartbeat")
def remote_node_heartbeat(
    node_id: str,
    body: RemoteNodeHeartbeatIn,
    request: Request,
) -> dict:
    registry = request.app.state.remote_nodes
    try:
        node = registry.reconnect(
            node_id,
            lease_seconds=body.lease_seconds,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _remote_node_dict(node)


@router.post("/remote/nodes/{node_id}/results")
def submit_remote_result(
    node_id: str,
    body: RemoteNodeResultIn,
    request: Request,
) -> dict:
    from runners.remote.models import NodeResult

    registry = request.app.state.remote_nodes
    result = registry.save_result(
        NodeResult(
            result_id=f"result_{uuid4().hex[:12]}",
            node_id=node_id,
            task_ref=body.task_ref,
            status=body.status,
            artifact_refs=tuple(body.artifact_refs),
            signature=body.signature,
            payload=body.payload,
        )
    )
    return _remote_result_dict(result)


@router.post("/remote/nodes/{node_id}/leases")
def lease_remote_task(
    node_id: str,
    body: RemoteNodeLeaseIn,
    request: Request,
) -> dict:
    registry = request.app.state.remote_nodes
    lease = registry.lease(
        node_id,
        body.task_ref,
        lease_seconds=body.lease_seconds,
    )
    return {
        "lease_id": lease.lease_id,
        "node_id": lease.node_id,
        "task_ref": lease.task_ref,
        "expires_at": lease.expires_at,
        "created_at": lease.created_at,
    }


@router.get("/remote/nodes/{node_id}/results")
def list_remote_results(node_id: str, request: Request) -> list[dict]:
    registry = getattr(request.app.state, "remote_nodes", None)
    if registry is None:
        return []
    return [
        _remote_result_dict(result)
        for result in registry.list_results(node_id)
    ]


@router.post("/remote/nodes/{node_id}/dispatch")
def dispatch_remote_task(
    node_id: str,
    body: RemoteNodeDispatchIn,
    request: Request,
) -> dict:
    registry = request.app.state.remote_nodes
    try:
        registry.get(node_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    lease = registry.save_dispatch(
        node_id,
        body.task_ref,
        body.payload,
        lease_seconds=body.lease_seconds,
    )
    return {
        "dispatch": {
            "node_id": node_id,
            "task_ref": body.task_ref,
            "payload": body.payload,
        },
        "lease": {
            "lease_id": lease.lease_id,
            "expires_at": lease.expires_at,
            "created_at": lease.created_at,
        },
    }


@router.get("/remote/nodes/{node_id}/tasks")
def list_remote_tasks(node_id: str, request: Request) -> list[dict]:
    registry = getattr(request.app.state, "remote_nodes", None)
    if registry is None:
        return []
    return [
        {
            "lease_id": task["lease_id"],
            "task_ref": task["task_ref"],
            "expires_at": task["expires_at"],
            "payload": task["payload"],
        }
        for task in registry.pending_tasks(node_id)
    ]


def _evidence_gate_summary(
    evidence,
    run_id: str,
    *,
    evidence_map=None,
    all_findings=None,
) -> dict:
    findings = evidence.list_findings_by_run(run_id)
    if evidence_map is None:
        evidence_map = evidence.evidence_map()
    open_statuses = {
        "candidate",
        "supported",
        "verified",
        "open",
        "retest_passed",
    }
    open_findings = [
        finding for finding in findings if finding.status.value in open_statuses
    ]
    if all_findings is None:
        all_findings = evidence.list_all_findings()
    verified_fingerprints = {
        finding.fingerprint
        for finding in all_findings
        if finding.status.value == "verified" and finding.fingerprint
    }
    open_findings += [
        finding
        for finding in findings
        if (
            finding.status.value == "duplicate"
            and finding.fingerprint in verified_fingerprints
        )
    ]
    verified = [
        finding for finding in findings if finding.status.value == "verified"
    ]
    verified_with_duplicates = verified + [
        finding
        for finding in findings
        if (
            finding.status.value == "duplicate"
            and finding.fingerprint in verified_fingerprints
        )
    ]
    replay_proven = [
        finding
        for finding in findings
        if finding in verified_with_duplicates
        or finding.retest_proof
        or any(
            evidence_map.get(evidence_id) is not None
            and bool(evidence_map[evidence_id].replay_proof)
            for evidence_id in finding.evidence_ids
        )
    ]

    def _gated(finding) -> bool:
        if finding.status.value == "verified":
            return True
        if (
            finding.status.value == "duplicate"
            and finding.fingerprint in verified_fingerprints
        ):
            return True
        return (
            finding.status.value == "retest_passed"
            and bool(finding.retest_proof.get("matched"))
        )

    gate_pass = bool(open_findings) and all(
        _gated(finding) and bool(finding.evidence_ids)
        for finding in open_findings
    )
    return {
        "run_id": run_id,
        "findings": len(findings),
        "open_findings": len(open_findings),
        "verified": len(verified_with_duplicates),
        "replay_proven": len(replay_proven),
        "gate_pass": gate_pass,
    }


def _finding_sources(findings) -> dict[str, int]:
    import re

    counts: dict[str, int] = {}
    for finding in findings:
        notes = str(getattr(finding, "notes", "") or "")
        match = re.search(r"parser=([A-Za-z0-9_.-]+)", notes)
        source = match.group(1) if match else "unknown"
        counts[source] = counts.get(source, 0) + 1
    return counts


@router.get("/runs/{run_id}/evidence-gate")
def evidence_gate(run_id: str, request: Request) -> dict:
    _require_run_access(request, run_id)
    return _evidence_gate_summary(
        request.app.state.evidence,
        run_id,
    )


@router.post("/runs/{run_id}/findings", response_model=Finding)
def submit_finding(
    run_id: str,
    body: SubmitFindingIn,
    request: Request,
) -> Finding:
    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    evidence = None
    if body.evidence is not None:
        evidence = Evidence(
            evidence_id=f"ev_{uuid4().hex[:12]}",
            source_type=body.evidence.source_type,
            target_ref=body.target_ref,
            occurred_at=body.evidence.occurred_at or utc_now(),
            action_ref=body.evidence.action_ref,
            tool_version=body.evidence.tool_version,
            artifact_refs=body.evidence.artifact_refs,
            replay_proof=body.evidence.replay_proof,
            parser_version=body.evidence.parser_version,
            confidence=body.evidence.confidence,
            redacted=body.evidence.redacted,
        )
    finding = request.app.state.evidence.submit_candidate(
        run_id=run_id,
        target_ref=body.target_ref,
        vuln_category=body.vuln_category,
        endpoint=body.endpoint,
        param=body.param,
        notes=body.notes,
        severity=body.severity,
        evidence=evidence,
    )
    try:
        project_id = _project_id_for_run(request, run_id)
        _upsert_assets_for_url(request, project_id, body.endpoint)
        for asset in request.app.state.assets.list(project_id):
            value = str(asset["value"])
            if value and (
                body.endpoint.startswith(value) or value in body.endpoint
            ):
                finding = request.app.state.evidence.update_metadata(
                    finding.finding_id,
                    asset_id=asset["asset_id"],
                )
                break
    except Exception:
        pass
    return finding


@router.post("/findings/{finding_id}/support", response_model=Finding)
def support_finding(finding_id: str, request: Request) -> Finding:
    _require_finding_access(request, finding_id)
    try:
        return request.app.state.evidence.support(finding_id)
    except (KeyError, ValueError) as error:
        status = 404 if isinstance(error, KeyError) else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.get("/findings/{finding_id}", response_model=Finding)
def get_finding(finding_id: str, request: Request) -> Finding:
    return _require_finding_access(request, finding_id)


@router.post("/findings/{finding_id}/verify", response_model=Finding)
def verify_finding(finding_id: str, body: VerifyFindingIn, request: Request) -> Finding:
    _require_finding_access(request, finding_id)
    try:
        return request.app.state.evidence.verify(finding_id, oracle=body.oracle)
    except (KeyError, ValueError) as error:
        status = 404 if isinstance(error, KeyError) else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post("/findings/{finding_id}/review", response_model=Finding)
def review_finding(finding_id: str, body: FindingDecisionIn, request: Request) -> Finding:
    _require_finding_access(request, finding_id)
    try:
        return request.app.state.evidence.review(
            finding_id,
            decision=body.decision,
            decided_by=body.decided_by,
        )
    except (KeyError, ValueError) as error:
        status = 404 if isinstance(error, KeyError) else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post("/findings/{finding_id}/retest", response_model=Finding)
def retest_finding(finding_id: str, body: RetestFindingIn, request: Request) -> Finding:
    _require_finding_access(request, finding_id)
    try:
        return request.app.state.evidence.retest(finding_id, proof=body.proof)
    except (KeyError, ValueError) as error:
        status = 404 if isinstance(error, KeyError) else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post("/findings/{finding_id}/notes", response_model=Finding)
def append_finding_note(
    finding_id: str,
    body: FindingNoteIn,
    request: Request,
) -> Finding:
    _require_finding_access(request, finding_id)
    try:
        return request.app.state.evidence.append_note(
            finding_id,
            body.note,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/runtime/runners")
def list_runners(request: Request) -> list[dict]:
    return request.app.state.registry.list("runners")


@router.post("/runtime/runners")
def register_runner(body: RegisterRunnerIn, request: Request) -> dict:
    return request.app.state.registry.upsert_runner(
        body.runner_id,
        body.kind,
        body.status,
    )


@router.post("/runtime/runners/import")
def import_runners(body: BatchImportIn, request: Request) -> dict:
    imported = 0
    for item in body.items:
        request.app.state.registry.upsert_runner(
            str(item.get("runner_id") or item.get("id") or ""),
            str(item.get("kind") or "control-plane"),
            str(item.get("status") or "online"),
        )
        imported += 1
    _audit(request, "runner.import", "*", f"count={imported}")
    return {"imported": imported}


@router.get("/runtime/providers")
def list_providers(request: Request) -> list[dict]:
    return request.app.state.registry.list("providers")


@router.delete("/runtime/providers/{provider_id}")
def delete_provider(provider_id: str, request: Request) -> dict:
    _require_role(request, "admin")
    deleted = request.app.state.registry.delete_provider(provider_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"provider {provider_id} not found")
    _audit(request, "provider.delete", provider_id)
    return {"deleted": True, "provider_id": provider_id}


@router.post("/runtime/providers")
def register_provider(body: RegisterProviderIn, request: Request) -> dict:
    _require_role(request, "admin", "operator")
    config = {
        "api_key_ref": body.api_key_ref,
        "backend": body.backend,
        "litellm_provider": body.litellm_provider,
        "timeout_seconds": body.timeout_seconds,
        "thinking_mode": body.thinking_mode,
        "reasoning_effort": body.reasoning_effort,
        "retries": body.retries,
        "streaming": body.streaming,
        "max_tokens": body.max_tokens,
        "headers": body.headers,
    }
    result = request.app.state.registry.upsert_provider(
        body.provider_id,
        body.model,
        body.endpoint,
        body.status,
        config=config,
    )
    _audit(
        request,
        "provider.upsert",
        body.provider_id,
        f"{body.model} @ {body.endpoint}",
    )
    return result


@router.get("/settings/provider-default")
def get_provider_default(request: Request) -> dict | None:
    return request.app.state.registry.get_setting("provider_default")


@router.post("/settings/provider-default")
def set_provider_default(body: ProviderDefaultIn, request: Request) -> dict:
    payload = body.model_dump()
    existing = next(
        (
            provider
            for provider in request.app.state.registry.list("providers")
            if provider.get("provider_id") == body.provider_id
        ),
        None,
    )
    request.app.state.registry.upsert_provider(
        body.provider_id,
        body.model,
        body.endpoint,
        "ok",
        config=(existing or {}).get("config") or None,
    )
    return request.app.state.registry.set_setting("provider_default", payload)


@router.get("/providers/presets")
def provider_presets() -> list[dict]:
    return list_provider_presets()


@router.get("/settings/retrieval")
def get_retrieval_settings(request: Request) -> dict | None:
    return request.app.state.registry.get_setting("retrieval_default")


@router.post("/settings/retrieval")
def set_retrieval_settings(body: dict, request: Request) -> dict:
    return request.app.state.registry.set_setting("retrieval_default", body)


@router.post("/settings/retrieval/test")
def test_retrieval_settings(
    body: dict | None = None,
    request: Request = None,
) -> dict:
    payload = (
        body
        if body is not None
        else request.app.state.registry.get_setting("retrieval_default")
        or {}
    )
    config = resolve_retrieval_config(payload)
    runtime_dir = Path(os.environ.get("VERIDIX_RUNTIME_DIR") or "runtime")
    return probe_retrieval_config(config, runtime_dir=runtime_dir)


@router.get("/runtime/tools")
def list_tools(request: Request) -> list[dict]:
    return request.app.state.registry.list("tools")


@router.get("/runtime/tool-environment")
def tool_environment(request: Request) -> dict:
    from services.identity_service.config_identity import (
        load_tool_environment,
    )

    runtime_dir = Path(os.environ.get("VERIDIX_RUNTIME_DIR") or "runtime")
    env = load_tool_environment(runtime_dir)
    if not env.get("available"):
        images = _local_docker_images()
        env = {
            "available": "ghcr.io/kxahcc/veridix/veridix-tools:full" in images,
            "image": "ghcr.io/kxahcc/veridix/veridix-tools:full",
            "digest": "",
            "packs": [],
            "health": "missing",
        }
    return env


@router.get("/runtime/tool-packs")
def list_tool_packs(request: Request) -> list[dict]:
    from services.agent_runtime.kernel.tool_pack import ToolRegistry

    pack_dir = Path(__file__).resolve().parents[3] / "deploy" / "toolpacks"
    registry = ToolRegistry()
    for path in sorted(pack_dir.glob("*.json")):
        try:
            registry.load_manifest(path)
        except Exception:
            continue
    images = _local_docker_images()
    packs: dict[str, dict] = {}
    for definition in registry.list():
        manifest = registry.pack_for(definition.ref)
        pack = packs.setdefault(
            manifest.name,
            {
                "name": manifest.name,
                "version": manifest.version,
                "image": manifest.image,
                "digest": manifest.digest,
                "license": manifest.license,
                "capabilities": list(manifest.capabilities),
                "runner_requirements": list(manifest.runner_requirements),
                "tools": [],
                "availability": _tool_availability(manifest, images),
            },
        )
        pack["tools"].append(
            {
                "ref": definition.ref,
                "name": definition.name,
                "description": definition.description,
                "risk_level": definition.risk_level,
                "capability": definition.capability,
                "runner": definition.runner,
                "sandbox_profile": definition.sandbox_profile,
                "timeout_seconds": definition.timeout_seconds,
                "examples": list(definition.examples),
                "failure_codes": list(definition.failure_codes),
            }
        )
    return list(packs.values())


@router.post("/runtime/tools")
def register_tool(body: RegisterToolIn, request: Request) -> dict:
    return request.app.state.registry.upsert_tool(
        body.tool_ref,
        body.capability,
        body.status,
    )


@router.delete("/runtime/tools/{tool_ref}")
def delete_tool(tool_ref: str, request: Request) -> dict:
    deleted = request.app.state.registry.delete_tool(tool_ref)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"tool {tool_ref} not found")
    return {"deleted": True, "tool_ref": tool_ref}


@router.get("/runtime/skills")
def list_skills(request: Request) -> list[dict]:
    by_ref: dict[str, dict] = {}
    for item in _list_builtin_skills():
        by_ref[str(item["name"])] = item
    for item in request.app.state.registry.list("skills"):
        ref = str(item.get("skill_ref") or item.get("name") or "")
        if ref and ref not in by_ref:
            by_ref[ref] = {**item, "skill_ref": ref}
    return list(by_ref.values())


@router.post("/runtime/skills/import-package")
async def import_skill_package_route(
    request: Request,
    file: UploadFile | None = File(default=None),
    skill_md: str = Form(default=""),
    overwrite: bool = Form(default=False),
) -> dict:
    from services.knowledge_service.skill_package import (
        import_skill_package,
    )

    if file is None and not skill_md.strip():
        raise HTTPException(
            status_code=400,
            detail="provide a .zip skill package or SKILL.md content",
        )
    content = await file.read() if file is not None else b""
    filename = file.filename or "skill.zip"
    target_root = (
        Path(os.environ.get("VERIDIX_RUNTIME_DIR", "runtime")) / "skills"
    )
    try:
        result = import_skill_package(
            content,
            target_root=target_root,
            filename=filename,
            skill_md=skill_md,
            overwrite=overwrite,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    request.app.state.registry.upsert_skill(
        result["skill_ref"],
        result["name"],
        result["version"],
        "available",
        trigger="",
        runner="container",
        risk_level="L1",
    )
    _audit(
        request,
        "skill.import",
        result["skill_ref"],
        f"version={result['version']} files={len(result['files'])}",
    )
    return result


@router.get("/runtime/skills/{skill_ref}")
def get_skill(skill_ref: str, request: Request) -> dict:
    builtin = _get_builtin_skill(skill_ref)
    if builtin is not None:
        return builtin
    row = request.app.state.registry.get_row("skills", skill_ref)
    if row is not None:
        return row
    raise HTTPException(status_code=404, detail=f"skill {skill_ref} not found")


@router.post("/runtime/skills")
def register_skill(body: RegisterSkillIn, request: Request) -> dict:
    return request.app.state.registry.upsert_skill(
        body.skill_ref,
        body.name,
        body.version,
        body.status,
        trigger=body.trigger,
        runner=body.runner,
        risk_level=body.risk_level,
    )


@router.delete("/runtime/skills/{skill_ref}")
def delete_skill(skill_ref: str, request: Request) -> dict:
    deleted = request.app.state.registry.delete_skill(skill_ref)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"skill {skill_ref} not found")
    return {"deleted": True, "skill_ref": skill_ref}


@router.get("/runtime/mcp")
def list_mcp(request: Request) -> list[dict]:
    return request.app.state.registry.list("mcp_servers")


@router.get("/runtime/mcp/presets")
def mcp_presets() -> list[dict]:
    return list_mcp_presets()


@router.post("/runtime/mcp")
def register_mcp(body: RegisterMcpIn, request: Request) -> dict:
    return request.app.state.registry.upsert_mcp(
        body.server_id,
        body.name,
        body.status,
        kind=body.kind,
        command=body.command,
        config={
            "description": body.description,
            "env": body.env,
            "timeout_seconds": body.timeout_seconds,
        },
    )


@router.post("/runtime/mcp/import")
def import_mcp_servers(body: BatchImportIn, request: Request) -> dict:
    imported = 0
    for item in body.items:
        server_id = str(item.get("server_id") or item.get("id") or "")
        if not server_id:
            continue
        request.app.state.registry.upsert_mcp(
            server_id,
            str(item.get("name") or server_id),
            str(item.get("status") or "available"),
            kind=str(item.get("kind") or "local"),
            command=str(item.get("command") or ""),
            config={
                "description": str(item.get("description") or ""),
                "env": dict(item.get("env") or {}),
                "timeout_seconds": item.get("timeout_seconds"),
            },
        )
        imported += 1
    _audit(request, "mcp.import", "*", f"count={imported}")
    return {"imported": imported}


@router.delete("/runtime/mcp/{server_id}")
def delete_mcp(server_id: str, request: Request) -> dict:
    deleted = request.app.state.registry.delete_mcp(server_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"mcp {server_id} not found")
    return {"deleted": True, "server_id": server_id}


@router.post("/runtime/mcp/{server_id}/test")
def test_mcp(server_id: str, request: Request) -> dict:
    row = request.app.state.registry.get_row("mcp_servers", server_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"mcp {server_id} not found")
    kind = str(row.get("kind") or "local")
    command = str(row.get("command") or "").strip()
    if kind in ("http", "sse") and command:
        url = command.split()[0]
        if url.startswith("http"):
            return _mcp_http_tools(url)
    if command:
        return _mcp_stdio_tools(command)
    return {"status": "unknown", "detail": "no testable command or url"}


@router.get("/diagnostics")
def diagnostics(request: Request) -> dict:
    lease = request.app.state.control.get_lease("agent-worker")
    worker_status = (
        "online" if lease is not None and lease.lease_until >= utc_now() else "lost"
    )
    return {
        "worker": {
            "status": worker_status,
            "lease": lease.model_dump() if lease is not None else None,
        },
        "runners": request.app.state.registry.list("runners"),
        "providers": request.app.state.registry.list("providers"),
        "provider_default": request.app.state.registry.get_setting(
            "provider_default"
        ),
        "tools": request.app.state.registry.list("tools"),
        "tool_environment": _tool_environment(),
        "storage": _storage_backends(),
        "product_identity": _product_identity(),
        "connectors": _connectors(request.app.state.control),
        "components": _health_components(request),
    }


@router.post("/diagnostics/self-check")
def diagnostics_self_check(request: Request) -> dict:
    lease = request.app.state.control.get_lease("agent-worker")
    worker_status = (
        "online"
        if lease is not None and lease.lease_until >= utc_now()
        else "lost"
    )
    providers = request.app.state.registry.list("providers")
    mcp = request.app.state.registry.list("mcp_servers")
    runners = request.app.state.registry.list("runners")
    components = _health_components(request)
    ok = all(
        str(component.get("status") or "missing") != "failed"
        for component in components.values()
    )
    return {
        "checked_at": utc_now(),
        "ok": ok,
        "worker": worker_status,
        "components": components,
        "counts": {
            "providers": len(providers),
            "mcp": len(mcp),
            "runners": len(runners),
        },
    }


@router.get("/acceptance")
def acceptance_summary() -> dict:
    results = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "results"
    )

    def load(name: str) -> dict:
        path = results / name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    external_fixture = load("preset-external-fixture-2026-08-05.json")
    if not external_fixture:
        external_fixture = {"real_environment": "pending"}
    preset_fixtures = load("preset-fixtures-2026-08-05.json")
    if not preset_fixtures:
        preset_fixtures = {
            "preset_count": len(LOOP_PRESET_REGISTRY.list())
        }

    return {
        "gates": load("real-provider-gates-2026-08-04.json"),
        "lab_gates": load("real-mission-gates-2026-08-03.json"),
        "rag": load("rag-qdrant-hybrid-2026-08-04.json"),
        "readiness": load("readiness-2026-08-04.json"),
        "tool_smoke": load("tool-image-smoke-2026-08-04.json"),
        "tool_matrix": load("real-tool-matrix-all-2026-08-06.json"),
        "mcp_real": load("mcp-real-smoke-2026-08-06.json"),
        "acceptance_all": load("acceptance-gate-2026-08-06-all.json"),
        "profile_engineering": {
            "deterministic": load(
                "loop-profile-context-bench-2026-08-05.json"
            ),
            "real_preset": load("profile-preset-real-2026-08-05.json"),
            "real_presets": {
                "nikto-focused": load(
                    "profile-preset-real-2026-08-05.json"
                ),
                "host-recon": load(
                    "profile-preset-host-recon-real-2026-08-05.json"
                ),
            },
            "external_fixture": external_fixture,
            "preset_fixtures": preset_fixtures,
            "preset_count": len(LOOP_PRESET_REGISTRY.list()),
        },
    }


@router.get("/runs/{run_id}/graph-metrics")
def run_graph_metrics(run_id: str, request: Request) -> dict:
    _require_run_access(request, run_id)
    try:
        events = request.app.state.events.replay(run_id)
    except Exception as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    completed = [
        event.payload
        for event in events
        if event.event_type == "graph.completed"
    ]
    return {
        "run_id": run_id,
        "event_count": len(events),
        "graph_completed": len(completed),
        "metrics": completed,
    }


@router.get("/runs/{run_id}/trace")
def run_trace(run_id: str, request: Request) -> dict:
    _require_run_access(request, run_id)
    try:
        run = request.app.state.control.get_run(run_id)
        events = request.app.state.events.replay(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    findings = request.app.state.evidence.list_findings_by_run(run_id)
    gate = _evidence_gate_summary(request.app.state.evidence, run_id)
    completed = [
        event.payload
        for event in events
        if event.event_type == "graph.completed"
    ]
    tool_events = [
        {
            "event_type": event.event_type,
            "sequence": event.sequence,
            "payload": event.payload,
        }
        for event in events
        if event.event_type.startswith("tool.")
        or event.event_type.startswith("loop.")
    ]
    return {
        "run_id": run_id,
        "status": run.status,
        "stop_reason": run.stop_reason,
        "created_at": run.created_at,
        "event_count": len(events),
        "event_types": [event.event_type for event in events],
        "events": [
            {
                "event_type": event.event_type,
                "sequence": event.sequence,
                "occurred_at": event.occurred_at,
                "payload": event.payload,
            }
            for event in events
        ],
        "tool_events": tool_events,
        "findings": [
            finding.model_dump(mode="json")
            for finding in findings
        ],
        "evidence_gate": gate,
        "graph_metrics": completed,
        "approval_required": sum(
            1
            for event in events
            if event.event_type == "graph.human.required"
        ),
        "memory_facts_appended": sum(
            1
            for event in events
            if event.event_type == "memory.fact.appended"
        ),
    }


@router.get("/runs/{run_id}/attack-graph")
def run_attack_graph(run_id: str, request: Request) -> dict:
    from services.mission_orchestrator.attack_graph import (
        build_attack_graph,
    )

    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    findings = request.app.state.evidence.list_findings_by_run(run_id)
    target_ref = next(
        (
            finding.target_ref
            for finding in findings
            if finding.target_ref
        ),
        f"run://{run_id}",
    )
    graph = build_attack_graph(
        target_ref=target_ref,
        findings=[
            {
                "endpoint": finding.endpoint,
                "vuln_category": finding.vuln_category,
            }
            for finding in findings
        ],
    )
    return graph.to_dict()


@router.get("/runs/{run_id}/human-gates")
def list_human_gates(run_id: str, request: Request) -> dict:
    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    events = request.app.state.events.replay(run_id)
    pending: list[dict] = []
    resolved: dict[str, dict] = {}
    for event in events:
        if event.event_type == "graph.human.required":
            pending.append(
                {
                    "node_id": event.payload.get("node_id"),
                    "prompt": event.payload.get("prompt", ""),
                }
            )
        elif event.event_type == "graph.human.resolved":
            node_id = event.payload.get("node_id")
            resolved[node_id] = {
                "approved": bool(event.payload.get("approved")),
                "reason": event.payload.get("reason", ""),
                "at": event.occurred_at,
            }
    pending = [
        gate for gate in pending if gate["node_id"] not in resolved
    ]
    return {"pending": pending, "resolved": resolved}


@router.post("/runs/{run_id}/human-gates/{node_id}/resolve")
def resolve_human_gate(
    run_id: str,
    node_id: str,
    body: HumanGateResolveIn,
    request: Request,
) -> dict:
    _require_run_access(request, run_id)
    try:
        request.app.state.control.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    request.app.state.events.append(
        AgentEvent(
            event_id=f"human-gate:{node_id}:{uuid4().hex[:8]}",
            event_type="graph.human.resolved",
            stream_id=run_id,
            run_id=run_id,
            actor="api/control",
            payload={
                "node_id": node_id,
                "approved": body.approved,
                "reason": body.reason,
            },
        )
    )
    return {
        "node_id": node_id,
        "approved": body.approved,
        "reason": body.reason,
    }


def _tool_environment() -> dict:
    runtime_dir = os.environ.get("VERIDIX_RUNTIME_DIR", "runtime")
    path = Path(runtime_dir) / "tool-environment.json"
    if not path.exists():
        return {"available": False, "digest": ""}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False, "digest": ""}
    if not isinstance(payload, dict):
        return {"available": False, "digest": ""}
    packs = payload.get("packs") or []
    pack_names = []
    for pack in packs:
        if isinstance(pack, dict):
            pack_names.append(str(pack.get("name") or ""))
        elif isinstance(pack, str):
            pack_names.append(pack)
    return {
        "available": True,
        "digest": str(payload.get("digest") or ""),
        "builder_version": str(payload.get("builder_version") or ""),
        "packs": [name for name in pack_names if name],
        "health": str(payload.get("health") or "unknown"),
    }


def _storage_backends() -> dict:
    runtime_dir = os.environ.get("VERIDIX_RUNTIME_DIR", "runtime")
    path = Path(runtime_dir) / "storage.json"
    if not path.exists():
        return {
            "available": False,
            "embedding": {"backend": "unknown"},
            "vector_store": {"type": "unknown"},
            "graph": {"enabled": False},
            "rerank": {"enabled": False},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False, "error": "storage snapshot unreadable"}
    return {
        "available": True,
        **payload,
    }


def _product_identity() -> dict:
    from services.identity_service.config_identity import (
        load_runtime_versions,
        product_identity_digest,
    )

    digest = product_identity_digest(
        tool_environment=_tool_environment(),
        runtime_versions=load_runtime_versions(),
    )
    return {
        "digest": digest,
        "runtime_dir": os.environ.get("VERIDIX_RUNTIME_DIR", "runtime"),
    }


_CONNECTOR_TTL_SECONDS = 10.0


def _connectors(control) -> dict[str, dict]:
    configured = {
        "zap": os.environ.get("VERIDIX_ZAP_URL", ""),
        "caido": os.environ.get("VERIDIX_CAIDO_URL", ""),
        "burp": os.environ.get("VERIDIX_BURP_URL", ""),
    }
    result: dict[str, dict] = {}
    for name, url in configured.items():
        if not url:
            result[name] = {
                "url": "",
                "status": "not_configured",
                "checked_at": "",
            }
            continue
        cached = control.get_connector_status(name)
        if cached is not None:
            checked_ts = time.mktime(
                time.strptime(
                    cached["checked_at"],
                    "%Y-%m-%dT%H:%M:%SZ",
                )
            )
            if time.time() - checked_ts < _CONNECTOR_TTL_SECONDS:
                result[name] = {
                    "url": cached["url"],
                    "status": cached["status"],
                    "checked_at": cached["checked_at"],
                }
                continue
        status, checked_at = _probe_connector(name, url)
        control.save_connector_status(
            name,
            url,
            status,
            checked_at,
        )
        result[name] = {
            "url": url,
            "status": status,
            "checked_at": checked_at,
        }
        continue
    return result


def _probe_connector(name: str, url: str) -> tuple[str, str]:
    checked_at = utc_now()
    try:
        if name == "zap":
            response = httpx.get(
                f"{url.rstrip('/')}/JSON/core/view/version/",
                params={
                    "apikey": os.environ.get(
                        "VERIDIX_ZAP_API_KEY",
                        "veridix-zap",
                    )
                },
                timeout=1.5,
                trust_env=False,
            )
        elif name == "caido":
            response = httpx.post(
                f"{url.rstrip('/')}/graphql",
                json={"query": "query { __typename }"},
                timeout=1.5,
                trust_env=False,
            )
        else:
            response = httpx.get(
                f"{url.rstrip('/')}/health",
                timeout=1.5,
                trust_env=False,
            )
        response.raise_for_status()
        return "ok", checked_at
    except Exception:
        return "unreachable", checked_at


@router.get("/runs/{run_id}/approvals", response_model=list[ApprovalRequest])
def list_approvals(run_id: str, request: Request) -> list[ApprovalRequest]:
    _require_run_access(request, run_id)
    return request.app.state.control.list_approvals(run_id)


@router.get("/audit-logs")
def list_audit_logs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = None,
    actor: str | None = None,
) -> list[dict]:
    store = getattr(request.app.state, "audit", None)
    if store is None:
        return []
    return store.list(limit=limit, action=action, actor=actor)


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalRequest)
def decide_approval(
    approval_id: str,
    body: ApprovalDecisionIn,
    request: Request,
) -> ApprovalRequest:
    _require_role(request, "admin", "operator")
    try:
        approval = request.app.state.control.get_approval(approval_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _require_run_access(request, approval.run_id)
    try:
        result = request.app.state.runs.decide_approval(
            approval_id,
            approved=body.approved,
            decided_by=body.decided_by,
            reason=body.reason,
        )
        _audit(
            request,
            "approval.decide",
            approval_id,
            f"approved={body.approved} by={body.decided_by}",
        )
        return result
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/leases/{worker_id}/heartbeat", response_model=LeaseRecord)
def lease_heartbeat(
    worker_id: str,
    body: LeaseHeartbeatIn,
    request: Request,
) -> LeaseRecord:
    return request.app.state.control.upsert_lease(worker_id, body.lease_seconds)


@router.post("/providers/probe")
def provider_probe(body: ProviderProbeIn, request: Request) -> dict:
    result = probe_provider(
        body.model_dump(),
        resolver=request.app.state.secret_resolver,
    )
    if body.run_id and result.get("event_type") == "rag_degraded":
        import uuid

        request.app.state.events.append(
            AgentEvent(
                event_id=f"{body.run_id}:rag_degraded:{uuid.uuid4().hex[:8]}",
                event_type="rag_degraded",
                stream_id=body.run_id,
                run_id=body.run_id,
                actor="api/control",
                payload={
                    "provider_id": body.provider_id,
                    "reason": result["reason"],
                },
            )
        )
    return result


@router.post("/providers/models")
def provider_models(body: ProviderProbeIn, request: Request) -> dict:
    models = list_provider_models(
        body.model_dump(),
        resolver=request.app.state.secret_resolver,
    )
    return {
        "provider_id": body.provider_id,
        "models": models,
    }


def _run_command(
    request: Request,
    run_id: str,
    body: RunCommandRequest,
    command: str,
) -> RunState:
    _require_run_access(request, run_id)
    try:
        service = request.app.state.runs
        if command == "pause":
            return service.pause(run_id, body.idempotency_key)
        if command == "resume":
            return service.resume(run_id, body.idempotency_key)
        return service.cancel(run_id, body.idempotency_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DomainError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _local_docker_images() -> set[str]:
    try:
        result = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        }
    except Exception:
        return set()


def _health_components(request: Request) -> dict:
    components: dict[str, dict] = {}
    components["pgvector"] = _tcp_component(
        int(os.environ.get("VERIDIX_PGVECTOR_PORT", "5433"))
    )
    components["qdrant"] = _http_component(
        os.environ.get("VERIDIX_QDRANT_URL", "http://127.0.0.1:6333")
    )
    components["chroma"] = _http_component(
        os.environ.get("VERIDIX_CHROMA_URL", "http://127.0.0.1:8001")
        + "/api/v2/version"
    )
    components["neo4j"] = _tcp_component(7687)
    embedding_endpoint = os.environ.get("VERIDIX_EMBEDDING_ENDPOINT", "")
    if embedding_endpoint:
        try:
            response = httpx.get(
                f"{embedding_endpoint.rstrip('/')}/models",
                timeout=2,
                trust_env=not (
                    embedding_endpoint.startswith("http://127.0.0.1")
                    or embedding_endpoint.startswith("http://localhost")
                ),
            )
            components["embedding"] = {
                "status": "ok" if response.status_code < 500 else "failed",
                "detail": os.environ.get("VERIDIX_EMBEDDING_MODEL", ""),
            }
        except Exception as error:
            components["embedding"] = {
                "status": "failed",
                "detail": f"{type(error).__name__}: {error}",
            }
    else:
        components["embedding"] = {
            "status": "not_configured",
            "detail": "",
        }
    images = _local_docker_images()
    components["tool_image"] = {
        "status": (
            "ok"
            if any(
                "veridix-tools" in image
                or "veridix-tools" in image
                for image in images
            )
            else "missing"
        ),
        "detail": f"{len(images)} 个本地镜像",
    }
    providers = request.app.state.registry.list("providers")
    components["providers"] = {
        "status": "ok" if providers else "not_configured",
        "detail": f"{len(providers)} 个供应商",
    }
    mcp = request.app.state.registry.list("mcp_servers")
    components["mcp"] = {
        "status": "ok" if mcp else "not_configured",
        "detail": f"{len(mcp)} 个 MCP 服务器",
    }
    runners = request.app.state.registry.list("runners")
    components["runner"] = {
        "status": "ok" if runners else "not_configured",
        "detail": f"{len(runners)} 个执行单元",
    }
    return components


def _tcp_component(port: int) -> dict:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return {"status": "ok", "detail": f"127.0.0.1:{port}"}
    except OSError as error:
        return {"status": "failed", "detail": str(error)}


def _http_component(url: str) -> dict:
    try:
        response = httpx.get(url, timeout=2, trust_env=False)
        return {
            "status": "ok" if response.status_code < 400 else "failed",
            "detail": f"HTTP {response.status_code}",
        }
    except Exception as error:
        return {"status": "failed", "detail": f"{type(error).__name__}"}


def _mcp_error_detail(error: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    queue = [error]
    while queue:
        current = queue.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        detail = str(current) or type(current).__name__
        if detail not in parts:
            parts.append(detail)
        children = getattr(current, "exceptions", None)
        if children:
            queue.extend(children)
        if len(parts) >= 8:
            break
    return " | ".join(parts) or str(error)


def _mcp_suggestion(command: str, error: BaseException) -> str:
    text = f"{type(error).__name__} {error}".lower()
    if command.strip().startswith("npx "):
        if any(
            token in text
            for token in (
                "enospc",
                "not found",
                "cannot find",
                "spawn",
                "unhandled",
                "taskgroup",
                "connection",
            )
        ):
            return "npx MCP 包未安装或网络不可达；先运行该命令安装包后再测试"
    if "timeout" in text or "deadline" in text:
        return "MCP 初始化超时；确认服务已启动且命令可独立运行"
    return "检查启动命令、依赖安装和服务日志"


def _mcp_stdio_tools(command: str) -> dict:
    import shlex

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parts = shlex.split(command)
    if not parts:
        return {"status": "unknown", "detail": "empty command", "tools": []}

    async def _run() -> list:
        params = StdioServerParameters(
            command=parts[0],
            args=parts[1:],
            env=None,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema or {},
                    }
                    for tool in result.tools
                ]

    try:
        tools = asyncio.run(asyncio.wait_for(_run(), timeout=25))
        return {
            "status": "ok",
            "detail": f"{len(tools)} tools",
            "tools": tools,
        }
    except BaseException as error:
        return {
            "status": "degraded",
            "detail": _mcp_error_detail(error),
            "suggestion": _mcp_suggestion(command, error),
            "tools": [],
        }


def _mcp_http_tools(url: str) -> dict:
    from mcp import ClientSession

    async def _run() -> list:
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema or {},
                    }
                    for tool in result.tools
                ]

    try:
        tools = asyncio.run(asyncio.wait_for(_run(), timeout=25))
        return {
            "status": "ok",
            "detail": f"{len(tools)} tools",
            "tools": tools,
        }
    except BaseException as error:
        return {
            "status": "degraded",
            "detail": _mcp_error_detail(error),
            "suggestion": _mcp_suggestion(url, error),
            "tools": [],
        }


def _tool_availability(manifest, images: set[str]) -> dict:
    image = str(manifest.image or "")
    if not image:
        return {
            "available": True,
            "status": "native",
            "detail": "本地/浏览器执行，无需镜像",
        }
    if image in images:
        return {
            "available": True,
            "status": "ok",
            "detail": f"镜像 {image} 已就绪",
        }
    return {
        "available": False,
        "status": "missing",
        "detail": f"缺少镜像 {image}",
    }


def _list_builtin_skills() -> list[dict]:
    result: list[dict] = []
    for skills_dir, prefix in _skill_directories():
        result.extend(_scan_skill_dir(skills_dir, prefix))
    return result


def _skill_directories() -> list[tuple[Path, str]]:
    builtin = Path(__file__).resolve().parents[3] / "skills" / "builtin"
    runtime = Path(
        os.environ.get("VERIDIX_RUNTIME_DIR", "runtime")
    ) / "skills"
    return [
        (builtin, "skills/builtin"),
        (runtime, "runtime/skills"),
    ]


def _scan_skill_dir(
    skills_dir: Path,
    package_prefix: str,
) -> list[dict]:
    result: list[dict] = []
    if not skills_dir.exists():
        return result
    for path in _builtin_skill_paths(skills_dir):
        data = _skill_payload(path)
        if data is None:
            continue
        result.append(
            _builtin_skill_view(
                path,
                data,
                skills_dir=skills_dir,
                package_prefix=package_prefix,
            )
        )
    return result


def _get_builtin_skill(skill_ref: str) -> dict | None:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", skill_ref)
    for skills_dir, prefix in _skill_directories():
        path = skills_dir / safe / "SKILL.md"
        if not path.exists():
            path = skills_dir / f"{safe}.md"
            if not path.exists():
                path = skills_dir / f"{safe}.json"
        if not path.exists():
            continue
        data = _skill_payload(path)
        if data is None:
            return None
        return _builtin_skill_view(
            path,
            data,
            skills_dir=skills_dir,
            package_prefix=prefix,
        )
    return None


def _builtin_skill_paths(skills_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for child in sorted(skills_dir.iterdir()):
        if child.is_dir():
            skill_file = child / "SKILL.md"
            if skill_file.exists():
                paths.append(skill_file)
    paths.extend(sorted(skills_dir.glob("*.md")))
    paths.extend(sorted(skills_dir.glob("*.json")))
    return paths


def _skill_payload(path: Path) -> dict | None:
    try:
        if path.suffix == ".md":
            return parse_skill_markdown(
                path.read_text(encoding="utf-8"),
                path_hint=str(path),
            )
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _builtin_skill_view(
    path: Path,
    data: dict,
    *,
    skills_dir: Path,
    package_prefix: str,
) -> dict:
    skill_ref = str(data.get("name") or path.stem)
    rel_dir = path.parent.relative_to(skills_dir).as_posix()
    if rel_dir == ".":
        package_path = f"{package_prefix}/{path.stem}"
        files: list[str] = []
    else:
        package_path = f"{package_prefix}/{rel_dir}"
        files = _package_skill_files(path.parent)
    return {
        "skill_ref": skill_ref,
        "name": skill_ref,
        "version": str(data.get("version") or "1"),
        "description": str(data.get("description") or ""),
        "category": str(data.get("category") or ""),
        "tags": list(data.get("tags") or ()),
        "cwe_ids": list(data.get("cwe_ids") or ()),
        "prerequisites": list(data.get("prerequisites") or ()),
        "chains_with": list(data.get("chains_with") or ()),
        "severity_boost": dict(data.get("severity_boost") or {}),
        "references": list(data.get("references") or ()),
        "authors": list(data.get("authors") or ()),
        "license": str(data.get("license") or ""),
        "trigger": data.get("trigger") or [],
        "required_tools": data.get("required_tools") or [],
        "required_runner": data.get("required_runner") or "",
        "risk_level": str(data.get("risk_level") or "L1"),
        "source": "builtin",
        "content": str(data.get("content", "")),
        "package_path": package_path,
        "files": files,
    }


def _package_skill_files(package_dir: Path) -> list[str]:
    if not package_dir.is_dir():
        return []
    files: list[str] = []
    for child in sorted(package_dir.rglob("*")):
        if not child.is_file():
            continue
        rel = child.relative_to(package_dir).as_posix()
        if rel == "SKILL.md":
            continue
        files.append(rel)
    return files
