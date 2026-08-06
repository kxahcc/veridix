from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .api import router
from .control_store import ControlStore
from .domain import new_id
from .event_store import CommandStore, EventStore
from .run_service import RunService
from .web_observation_store import WebObservationStore
from .artifact_store import ArtifactStore
from .secrets import SecretResolver
from .registry import RuntimeRegistry
from .asset_store import AssetStore
from .session_store import SessionStore
from .audit_store import AuditLogStore
from services.evidence_service.evidence_store import EvidenceStore
from services.evidence_service.service import EvidenceService
from services.knowledge_service.knowledge_store import KnowledgeStore
from services.knowledge_service.graph_store import KnowledgeGraphStore
from services.knowledge_service.import_pipeline import SourceRegistry
from services.knowledge_service.sqlite_memory import ProjectMemoryStore
from runners.remote.registry import RemoteNodeRegistry


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    runtime_dir: str


class LoginRequest(BaseModel):
    token: str


class LoginResponse(BaseModel):
    authenticated: bool
    role: str = ""
    projects: list[str] = []
    requires_auth: bool = True


class AuthStatusResponse(BaseModel):
    requires_auth: bool
    authenticated: bool = False


def create_app(db_path: str = ":memory:") -> FastAPI:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    runtime_dir = Path(os.environ.get("VERIDIX_RUNTIME_DIR", "runtime"))
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "knowledge.db").parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    events = EventStore(db_path)
    commands = CommandStore(db_path)
    control = ControlStore(events, commands, db_path)
    runs = RunService(events, commands, control)
    web_observations = WebObservationStore(db_path)
    evidence = EvidenceService(EvidenceStore(db_path))
    artifact_store = ArtifactStore(
        Path(os.environ.get("VERIDIX_RUNTIME_DIR", "runtime")) / "artifacts"
    )
    secret_resolver = SecretResolver(
        Path(os.environ.get("VERIDIX_RUNTIME_DIR", "runtime")) / "secrets" / "refs.json"
    )
    registry = RuntimeRegistry(db_path)
    assets = AssetStore(db_path)
    sessions = SessionStore(db_path)
    audit = AuditLogStore(db_path)
    knowledge = KnowledgeStore(
        ":memory:"
        if db_path == ":memory:"
        else (
            runtime_dir / "knowledge.db"
        )
    )
    knowledge_graph = KnowledgeGraphStore(
        ":memory:"
        if db_path == ":memory:"
        else (runtime_dir / "knowledge-graph.db")
    )
    remote_nodes = RemoteNodeRegistry(
        ":memory:"
        if db_path == ":memory:"
        else (runtime_dir / "remote-nodes.db")
    )
    knowledge_sources = SourceRegistry(
        ":memory:"
        if db_path == ":memory:"
        else (runtime_dir / "knowledge.db")
    )
    memory_store = ProjectMemoryStore(
        ":memory:"
        if db_path == ":memory:"
        else (runtime_dir / "memory.db")
    )
    application = FastAPI(
        title="veridix control plane",
        version="0.1.0",
    )
    application.state.events = events
    application.state.commands = commands
    application.state.control = control
    application.state.runs = runs
    application.state.web_observations = web_observations
    application.state.evidence = evidence
    application.state.artifact_store = artifact_store
    application.state.secret_resolver = secret_resolver
    application.state.registry = registry
    application.state.assets = assets
    application.state.sessions = sessions
    application.state.audit = audit
    application.state.knowledge = knowledge
    application.state.knowledge_graph = knowledge_graph
    application.state.remote_nodes = remote_nodes
    application.state.knowledge_sources = knowledge_sources
    application.state.memory = memory_store
    application.state.project_id = new_id("project")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="api/control",
            version="0.1.0",
            runtime_dir=os.environ.get("VERIDIX_RUNTIME_DIR", "runtime"),
        )

    @application.get("/api/v1/auth/status", response_model=AuthStatusResponse)
    def auth_status() -> AuthStatusResponse:
        users_raw = os.environ.get("VERIDIX_CONTROL_USERS")
        expected = os.environ.get("VERIDIX_CONTROL_TOKEN")
        return AuthStatusResponse(
            requires_auth=bool(users_raw or expected),
        )

    @application.post("/api/v1/auth/login", response_model=LoginResponse)
    def auth_login(body: LoginRequest) -> LoginResponse:
        users_raw = os.environ.get("VERIDIX_CONTROL_USERS")
        if users_raw:
            try:
                users = json.loads(users_raw)
            except json.JSONDecodeError:
                users = {}
            identity = users.get(body.token)
            if isinstance(identity, dict):
                return LoginResponse(
                    authenticated=True,
                    role=str(identity.get("role") or "operator"),
                    projects=[
                        str(item)
                        for item in (identity.get("projects") or ())
                    ],
                    requires_auth=True,
                )
            raise HTTPException(status_code=401, detail="invalid token")
        expected = os.environ.get("VERIDIX_CONTROL_TOKEN")
        if expected:
            if body.token != expected:
                raise HTTPException(status_code=401, detail="invalid token")
            return LoginResponse(
                authenticated=True,
                role=os.environ.get("VERIDIX_CONTROL_ROLE", "admin"),
                requires_auth=True,
            )
        return LoginResponse(
            authenticated=False,
            requires_auth=False,
        )

    application.include_router(router)
    return application


app = create_app(os.environ.get("VERIDIX_CONTROL_DB", "runtime/control.sqlite3"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("VERIDIX_CONTROL_PORT", "8787")),
        log_level="warning",
    )
