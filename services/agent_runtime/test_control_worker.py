from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import http.client

from services.agent_runtime.control_worker import (
    _ActiveRun,
    _derive_allowed_tools,
    _load_tool_registry,
    _product_identity_for_spec,
    _tool_schema_map,
    _with_memory_tools,
    ControlPlaneClient,
    ControlPlaneEventSink,
    ControlPlaneRunWorker,
    SpoolOverflow,
    WorkerOptions,
    WorkerControlError,
    scanner_verify_policy,
)
from services.agent_runtime.kernel.contracts import (
    AgentRunSpec,
    ExecutionRequest,
    FactRecord,
    LoopEvent,
    LoopSpec,
)
from services.agent_runtime.kernel.fake_runner import FakeRunner
from services.agent_runtime.kernel.memory import InMemoryEventSink
from services.knowledge_service.models import FactRecord as MemoryFactRecord
from services.knowledge_service.sqlite_memory import SqliteProjectMemory


def _wait_ready(server: ThreadingHTTPServer, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=2.0,
            )
            connection.request("GET", "/api/v1/runs")
            response = connection.getresponse()
            response.read()
            connection.close()
            return
        except OSError as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(
        f"mock server on port {server.server_port} did not become ready: "
        f"{type(last_error).__name__}: {last_error}"
    )


def test_worker_writes_mature_storage_snapshot_at_startup(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("VERIDIX_STORAGE_PROFILE", "server")
    monkeypatch.setenv("VERIDIX_VECTOR_BACKEND", "qdrant")
    monkeypatch.setenv("VERIDIX_QDRANT_URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("VERIDIX_NEO4J_URI", "bolt://127.0.0.1:7687")
    monkeypatch.setenv("VERIDIX_NEO4J_PASSWORD", "veridixpass")
    monkeypatch.setenv(
        "VERIDIX_EMBEDDING_ENDPOINT",
        "http://127.0.0.1:11434/v1",
    )
    monkeypatch.setenv("VERIDIX_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("VERIDIX_RERANK_ENABLED", "1")

    class SnapshotClient:
        def get_retrieval_default(self) -> None:
            return None

    worker = ControlPlaneRunWorker(
        SnapshotClient(),
        options=WorkerOptions(runtime_dir=str(tmp_path)),
    )

    snapshot = json.loads(
        (tmp_path / "storage.json").read_text(encoding="utf-8")
    )
    assert snapshot["vector_store"]["type"] == "qdrant"
    assert snapshot["graph"]["backend"] == "neo4j"
    assert snapshot["embedding"]["backend"] == "openai_compatible"
    assert snapshot["rerank"]["enabled"] is True


def test_worker_registers_runner_at_startup(monkeypatch) -> None:
    monkeypatch.setenv("VERIDIX_RUNNER", "docker")

    class RegisterClient:
        def __init__(self) -> None:
            self.registered: list[tuple[str, str, str]] = []

        def get_retrieval_default(self) -> None:
            return None

        def register_runner(
            self,
            runner_id: str,
            kind: str,
            status: str = "online",
        ) -> None:
            self.registered.append((runner_id, kind, status))

    client = RegisterClient()
    worker = ControlPlaneRunWorker(
        client,
        options=WorkerOptions(worker_id="agent-worker"),
    )

    assert client.registered == [("agent-worker", "docker", "online")]


class MockControlHandler(BaseHTTPRequestHandler):
    state: dict = {
        "run_id": "run_worker_test",
        "mission_id": "mission_1",
        "target_id": "target_1",
        "status": "queued",
        "events": [],
        "finding": None,
        "provider_endpoint": "http://provider.test/v1",
    }
    reject_events = False

    def _send(self, obj: object, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _run(self) -> dict:
        return {
            "run_id": self.state["run_id"],
            "mission_id": self.state["mission_id"],
            "source_run_id": None,
            "status": self.state["status"],
            "event_count": len(self.state["events"]),
            "observations": [],
            "stop_reason": None,
            "created_at": "2026-08-02T00:00:00Z",
        }

    def do_GET(self) -> None:
        if self.path == "/api/v1/runs":
            self._send([self._run()])
            return
        if self.path.startswith("/api/v1/runs/"):
            self._send(self._run())
            return
        if self.path.startswith("/api/v1/missions/"):
            spec = {
                "target_id": self.state["target_id"],
                "provider": {
                    "endpoint": self.state["provider_endpoint"],
                    "model": "mock",
                },
                "mission": "Use shell.probe once, then run.finish.",
                "golden": True,
            }
            spec.update(self.state.get("mission_spec", {}))
            self._send(
                {
                    "mission_id": self.state["mission_id"],
                    "project_id": "project_1",
                    "name": "golden",
                    "spec": spec,
                    "created_at": "2026-08-02T00:00:00Z",
                }
            )
            return
        if self.path.startswith("/api/v1/targets/"):
            self._send(
                {
                    "target_id": self.state["target_id"],
                    "project_id": "project_1",
                    "url": "https://lab.example.test",
                    "allowed": [],
                    "excluded": [],
                    "authorization": "authorized",
                    "created_at": "2026-08-02T00:00:00Z",
                }
            )
            return
        self._send({"detail": "not found"}, 404)

    def do_POST(self) -> None:
        body = self._read()
        if self.path.endswith("/claim"):
            self.state["status"] = "running"
            self._send(self._run())
            return
        if self.path.endswith("/pause"):
            self.state["status"] = "paused"
            self._send(self._run())
            return
        if self.path.endswith("/finish"):
            self.state["status"] = body["outcome"]
            self._send(self._run())
            return
        if self.path.endswith("/runtime/providers"):
            self.state.setdefault("providers", []).append(body)
            self._send(body)
            return
        if self.path.endswith("/events"):
            if type(self).reject_events:
                self._send({"detail": "control plane unavailable"}, 503)
                return
            self.state["events"].append(body)
            self._send(body)
            return
        if self.path.endswith("/findings"):
            finding = {
                "finding_id": "finding_1",
                "run_id": body.get("run_id", self.state["run_id"]),
                "target_ref": body.get("target_ref", ""),
                "vuln_category": body.get("vuln_category", ""),
                "endpoint": body.get("endpoint", ""),
                "param": "",
                "status": "candidate",
                "fingerprint": "sha256:mock",
                "evidence_ids": [],
                "notes": body.get("notes", ""),
                "created_at": "2026-08-02T00:00:00Z",
                "updated_at": "2026-08-02T00:00:00Z",
                "retest_proof": {},
            }
            self.state["finding"] = finding
            self._send(finding)
            return
        if self.path.endswith("/support"):
            self.state["finding"]["status"] = "supported"
            self._send(self.state["finding"])
            return
        if self.path.endswith("/verify"):
            self.state["finding"]["status"] = "verified"
            self._send(self.state["finding"])
            return
        self._send({"detail": "not found"}, 404)

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


class MockProviderHandler(BaseHTTPRequestHandler):
    block_request: threading.Event | None = None
    release: threading.Event | None = None
    request_seen: threading.Event | None = None

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        has_tool_result = any(
            message.get("role") == "tool" for message in body.get("messages", [])
        )
        if (
            type(self).block_request is not None
            and not has_tool_result
        ):
            if type(self).request_seen is not None:
                type(self).request_seen.set()
            if not type(self).release.wait(timeout=15):
                self.send_error(504)
                return
        if has_tool_result:
            message = {
                "role": "assistant",
                "content": "probe complete",
            }
        else:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_mock_probe",
                        "type": "function",
                        "function": {
                            "name": "shell_probe",
                            "arguments": json.dumps(
                                {"target": "https://lab.example.test"}
                            ),
                        },
                    }
                ],
            }
        payload = json.dumps(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "created": 0,
                "model": "mock",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls"
                        if message.get("tool_calls")
                        else "stop",
                        "message": message,
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


def test_worker_claims_and_completes_control_plane_run(tmp_path) -> None:
    MockControlHandler.state = {
        "run_id": "run_worker_test",
        "mission_id": "mission_1",
        "target_id": "target_1",
        "status": "queued",
        "events": [],
        "finding": None,
        "provider_endpoint": "http://provider.test/v1",
        "mission_spec": {
            "mission": "admin panel default credentials on web discovery",
            "allowed_tools": [
                "shell.probe",
                "browser.open",
                "proxy.list",
                "web.replay",
                "run.finish",
            ]
        },
    }
    MockControlHandler.reject_events = False
    MockProviderHandler.block_request = None
    MockProviderHandler.release = None
    control = ThreadingHTTPServer(("127.0.0.1", 0), MockControlHandler)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), MockProviderHandler)
    control_thread = threading.Thread(target=control.serve_forever, daemon=True)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    control_thread.start()
    provider_thread.start()
    _wait_ready(control)
    _wait_ready(provider)
    try:
        runners: list[FakeRunner] = []

        def runner_factory() -> FakeRunner:
            runner = FakeRunner()
            runners.append(runner)
            return runner

        client = ControlPlaneClient(
            f"http://127.0.0.1:{control.server_port}"
        )
        worker = ControlPlaneRunWorker(
            client,
            runner_factory=runner_factory,
            options=WorkerOptions(
                provider_endpoint=f"http://127.0.0.1:{provider.server_port}/v1",
                provider_model="mock",
                golden_finding=True,
                memory_db=str(tmp_path / "memory.db"),
            ),
        )
        MockControlHandler.state["provider_endpoint"] = (
            f"http://127.0.0.1:{provider.server_port}/v1"
        )
        claimed = worker.poll_once()

        assert claimed == ["run_worker_test"]
        assert len(runners) == 1
        assert len(runners[0].executions) >= 1
        assert MockControlHandler.state["status"] == "succeeded"
        event_types = [
            event["event_type"]
            for event in MockControlHandler.state["events"]
        ]
        assert "harness.snapshot" in event_types
        assert "behavior.snapshot" in event_types
        assert "model.turn.started" in event_types
        assert "run.succeeded" not in event_types
        harness = next(
            event
            for event in MockControlHandler.state["events"]
            if event["event_type"] == "harness.snapshot"
        )
        assert harness["payload"]["tool_projection_digest"]
        assert "shell.probe" in harness["payload"]["included_tools"]
        context = next(
            event
            for event in MockControlHandler.state["events"]
            if event["event_type"] == "context.projection"
        )
        assert context["payload"]["retrieval"]["level"] == "lexical"
        assert context["payload"]["knowledge"]["included"]
        included_skills = {
            skill["name"]
            for skill in context["payload"]["skills"]["included"]
        }
        assert included_skills.intersection(
            {"web-discovery", "verifier"}
        )
        assert harness["payload"]["context_digest"]
        assert harness["payload"]["knowledge_refs"]
        assert (tmp_path / "memory.db").exists()
        assert MockControlHandler.state["finding"]["status"] == "verified"
        assert MockControlHandler.state["providers"][0]["model"] == "mock"
    finally:
        MockProviderHandler.request_seen = None
        control.shutdown()
        provider.shutdown()
        control.server_close()
        provider.server_close()
        control_thread.join(timeout=5)
        provider_thread.join(timeout=5)


def test_scanner_verify_policy_extracts_mission_spec_policy() -> None:
    policy = scanner_verify_policy(
        {
            "min_severity": "high",
            "require_evidence": False,
            "required_metadata_fields": ["cwe"],
            "dedupe": False,
            "conflict_blocks": False,
        }
    )

    assert policy["min_severity"] == "high"
    assert policy["require_evidence"] is False
    assert policy["required_metadata_fields"] == ("cwe",)
    assert policy["dedupe"] is False
    assert policy["conflict_blocks"] is False

    defaults = scanner_verify_policy({})
    assert defaults["min_severity"] == ""
    assert defaults["require_evidence"] is True
    assert defaults["dedupe"] is True
    assert defaults["conflict_blocks"] is True


def test_derive_allowed_tools_uses_explicit_list() -> None:
    assert _derive_allowed_tools(
        {"allowed_tools": ["web.nikto.scan", "run.finish"]},
        ("shell.probe", "run.finish"),
    ) == ("web.nikto.scan", "run.finish", "skill.read")


def test_derive_allowed_tools_falls_back_to_scanner_tools() -> None:
    assert _derive_allowed_tools(
        {"scanner_tools": ["code.sast.semgrep", "code.secrets.detect"]},
        ("shell.probe", "run.finish"),
    ) == (
        "code.sast.semgrep",
        "code.secrets.detect",
        "run.finish",
        "skill.read",
    )


def test_derive_allowed_tools_falls_back_to_code_tools() -> None:
    assert _derive_allowed_tools(
        {"code_tools": ["code.sast.semgrep"]},
        ("shell.probe", "run.finish"),
    ) == ("code.sast.semgrep", "run.finish", "skill.read")


def test_with_memory_tools_appends_agent_memory_refs() -> None:
    tools = _with_memory_tools(("web.nikto.scan", "run.finish"))
    assert "memory.recall" in tools
    assert "memory.record" in tools
    assert "memory.status" in tools
    assert tools.index("web.nikto.scan") < tools.index("memory.recall")


def test_with_memory_runner_writes_and_invalidates_context() -> None:
    memory = SqliteProjectMemory(":memory:", "project_memory_tool")
    worker = object.__new__(ControlPlaneRunWorker)
    worker._memory_for_project = lambda project_id: memory
    worker._context_projector_for = lambda mission: SimpleNamespace(
        memory_embedding=None
    )
    worker._assembled = {
        "run_memory_tool:node_discovery": object(),
        "run_other:node": object(),
    }
    base = SimpleNamespace(
        execute=lambda request: None,
        observations=lambda: [],
        replay_proofs=lambda: {},
    )

    wrapped = worker._with_memory_runner(
        base,
        {"project_id": "project_memory_tool"},
        "run_memory_tool",
    )
    result = wrapped.execute(
        ExecutionRequest(
            action_id="memory_record_1",
            run_id="run_memory_tool",
            tool_ref="memory.record",
            input={
                "subject": "/api/admin",
                "predicate": "observed:authz.test",
                "value": "admin endpoint accepts low privilege role",
            },
            idempotency_key="run_memory_tool:memory.record:1",
        )
    )

    assert result.status == "completed"
    assert json.loads(result.stdout)["inserted"] is True
    assert "run_memory_tool:node_discovery" not in worker._assembled
    assert "run_other:node" in worker._assembled


def test_tool_schema_map_exposes_pack_and_native_tools() -> None:
    registry = _load_tool_registry()

    schemas = _tool_schema_map(
        registry,
        (
            "web.nikto.scan",
            "run.finish",
            "skill.read",
            "memory.recall",
            "memory.record",
            "memory.status",
        ),
    )

    assert schemas["web.nikto.scan"]["properties"]["url"]["type"] == "string"
    assert schemas["run.finish"]["required"] == ["summary"]
    assert schemas["skill.read"]["required"] == ["skill_ref", "path"]
    assert schemas["memory.record"]["required"] == [
        "subject",
        "predicate",
        "value",
    ]


def test_tool_schema_map_respects_node_scope() -> None:
    registry = _load_tool_registry()

    schemas = _tool_schema_map(registry, ("web.replay",))

    assert set(schemas) == {"web.replay"}


def test_product_identity_changes_with_config_and_tool_environment(
    tmp_path,
) -> None:
    (tmp_path / "tool-environment.json").write_text(
        json.dumps({"digest": "env_1"}),
        encoding="utf-8",
    )
    base = _product_identity_for_spec(
        {"config": {"security": {"targetScope": {"allowed": []}}}},
        tmp_path,
    )
    changed_config = _product_identity_for_spec(
        {"config": {"security": {"targetScope": {"allowed": ["https://a"]}}}},
        tmp_path,
    )
    (tmp_path / "tool-environment.json").write_text(
        json.dumps({"digest": "env_2"}),
        encoding="utf-8",
    )
    changed_env = _product_identity_for_spec({}, tmp_path)

    assert base != changed_config
    assert base != changed_env


def test_worker_materializes_graph_findings_in_control_plane() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.submitted: list[dict] = []
            self.supported: list[str] = []
            self.verified: list[str] = []

        def submit_finding(self, run_id, **hint):
            finding = {
                "finding_id": f"finding_{len(self.submitted)}",
                "run_id": run_id,
                **hint,
            }
            self.submitted.append(finding)
            return finding

        def support_finding(self, finding_id):
            self.supported.append(finding_id)
            finding = self.submitted[-1]
            return {**finding, "status": "supported"}

        def verify_finding(self, finding_id, oracle="verified"):
            self.verified.append(finding_id)
            return {
                "finding_id": finding_id,
                "status": "verified",
            }
            finding = self.submitted[-1]
            return {**finding, "status": "verified"}

    client = StubClient()
    worker = ControlPlaneRunWorker(
        client,
        options=WorkerOptions(memory_db=":memory:"),
    )
    spec = AgentRunSpec(
        run_id="run_graph_findings",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("web.nikto.scan",),
        mission="scan and verify",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    events = InMemoryEventSink()
    active = _ActiveRun(
        run_id="run_graph_findings",
        agent_spec=spec,
        kernel=None,
        runner=None,
        events=events,
    )
    facts = (
        FactRecord(
            fact_id="fact_xss",
            subject="http://compose-dvwa-1/",
            predicate="finding",
            value="Exposure",
            source_refs=("artifact://scan/1",),
            metadata={
                "source": "nikto",
                "severity": "low",
                "matched_evidence": "cookie without httponly",
            },
        ),
        FactRecord(
            fact_id="fact_xss_dup",
            subject="http://compose-dvwa-1/",
            predicate="finding",
            value="Exposure",
            source_refs=("artifact://scan/2",),
            metadata={
                "source": "nikto",
                "severity": "low",
                "matched_evidence": "cookie without httponly",
            },
        ),
        FactRecord(
            fact_id="fact_noise",
            subject="http://compose-dvwa-1/",
            predicate="observed:web.nikto.scan",
            value="done",
        ),
    )

    rows = worker._submit_graph_findings(active, facts)

    assert len(rows) == 1
    assert client.supported == ["finding_0"]
    assert client.verified == ["finding_0"]
    assert rows[0]["status"] == "verified"
    assert rows[0]["vuln_category"] == "Exposure"
    assert rows[0]["evidence"]["artifact_refs"] == ["artifact://scan/1"]
    assert any(
        event.event_type == "finding.graph.verified"
        for event in events.replay("run_graph_findings")
    )


def test_worker_submits_parsed_findings_without_unbound_local_error() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.submitted: list[dict] = []
            self.supported: list[str] = []

        def submit_finding(self, run_id, **hint):
            finding = {
                "finding_id": f"finding_{len(self.submitted)}",
                "run_id": run_id,
                **hint,
            }
            self.submitted.append(finding)
            return finding

        def support_finding(self, finding_id):
            self.supported.append(finding_id)

    class StubKernel:
        def __init__(self) -> None:
            self._observations = [
                {
                    "tool": "web.nikto.scan",
                    "tool_call_id": "call_nikto_1",
                    "artifact_refs": ["artifact://scan/nikto/1"],
                    "parsed_observations": [
                        {
                            "kind": "finding",
                            "vuln_category": "Exposure",
                            "url": "http://compose-dvwa-1/",
                            "source": "nikto",
                            "rule_id": "cookies-without-httponly",
                            "confidence": 0.9,
                        }
                    ],
                },
                {
                    "tool": "web.nikto.scan",
                    "tool_call_id": "call_nikto_2",
                    "artifact_refs": [],
                    "parsed_observations": [],
                },
            ]

        def observations(self):
            return self._observations

    client = StubClient()
    worker = ControlPlaneRunWorker(
        client,
        options=WorkerOptions(memory_db=":memory:"),
    )
    spec = AgentRunSpec(
        run_id="run_parsed_findings",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("web.nikto.scan",),
        mission="scan and report",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    events = InMemoryEventSink()
    active = _ActiveRun(
        run_id="run_parsed_findings",
        agent_spec=spec,
        kernel=StubKernel(),
        runner=None,
        events=events,
        project_id="project_1",
    )

    worker._submit_parsed_findings(active)

    assert len(client.submitted) == 1
    assert client.submitted[0]["vuln_category"] == "Exposure"
    assert client.supported == ["finding_0"]
    emitted = [event.event_type for event in events.replay("run_parsed_findings")]
    assert "finding.connector.candidate" in emitted
    assert "memory.fact.appended" in emitted


def test_worker_marks_duplicate_graph_findings_without_failure() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.verified: list[str] = []

        def submit_finding(self, run_id, **hint):
            return {
                "finding_id": "finding_dup",
                "run_id": run_id,
                "status": "duplicate",
                **hint,
            }

        def support_finding(self, finding_id):
            raise RuntimeError("duplicate")

        def verify_finding(self, finding_id, oracle="verified"):
            self.verified.append(finding_id)
            return {
                "finding_id": finding_id,
                "status": "verified",
            }

    client = StubClient()
    worker = ControlPlaneRunWorker(
        client,
        options=WorkerOptions(memory_db=":memory:"),
    )
    spec = AgentRunSpec(
        run_id="run_graph_dup",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("web.nikto.scan",),
        mission="scan and verify",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    events = InMemoryEventSink()
    active = _ActiveRun(
        run_id="run_graph_dup",
        agent_spec=spec,
        kernel=None,
        runner=None,
        events=events,
    )
    facts = (
        FactRecord(
            fact_id="fact_dup",
            subject="http://compose-dvwa-1/",
            predicate="finding",
            value="Exposure",
            source_refs=("artifact://scan/1",),
            metadata={"source": "nikto", "severity": "low"},
        ),
    )

    rows = worker._submit_graph_findings(active, facts)

    event_types = {
        event.event_type
        for event in events.replay("run_graph_dup")
    }
    assert "finding.graph.duplicate" in event_types
    assert "finding.graph.failed" not in event_types
    assert rows[0]["status"] == "verified"
    assert client.verified == ["finding_dup"]


def test_worker_pauses_and_resumes_mid_run_from_file_checkpoint(
    tmp_path,
) -> None:
    MockControlHandler.state = {
        "run_id": "run_worker_test",
        "mission_id": "mission_1",
        "target_id": "target_1",
        "status": "queued",
        "events": [],
        "finding": None,
        "provider_endpoint": "http://provider.test/v1",
    }
    MockControlHandler.reject_events = False
    MockProviderHandler.block_request = threading.Event()
    MockProviderHandler.release = threading.Event()
    MockProviderHandler.request_seen = threading.Event()
    control = ThreadingHTTPServer(("127.0.0.1", 0), MockControlHandler)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), MockProviderHandler)
    control_thread = threading.Thread(target=control.serve_forever, daemon=True)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    control_thread.start()
    provider_thread.start()
    _wait_ready(control)
    _wait_ready(provider)
    try:
        client = ControlPlaneClient(
            f"http://127.0.0.1:{control.server_port}"
        )
        worker = ControlPlaneRunWorker(
            client,
            runner_factory=lambda: FakeRunner(),
            options=WorkerOptions(
                provider_endpoint=f"http://127.0.0.1:{provider.server_port}/v1",
                provider_model="mock",
                checkpoint_dir=str(tmp_path),
            ),
        )
        MockControlHandler.state["provider_endpoint"] = (
            f"http://127.0.0.1:{provider.server_port}/v1"
        )

        poll_thread = threading.Thread(
            target=lambda: worker.poll_once(),
            daemon=True,
        )
        poll_thread.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            if MockProviderHandler.request_seen.is_set():
                break
            time.sleep(0.05)
        MockControlHandler.state["status"] = "paused"
        deadline = time.time() + 10
        while time.time() < deadline:
            active = worker._active.get("run_worker_test")
            if active is not None and active.local_paused:
                break
            time.sleep(0.05)

        MockProviderHandler.release.set()
        poll_thread.join(timeout=15)
        assert not poll_thread.is_alive()
        assert MockControlHandler.state["status"] == "paused"
        assert MockControlHandler.state["finding"] is None

        MockControlHandler.state["status"] = "running"
        worker2 = ControlPlaneRunWorker(
            client,
            runner_factory=lambda: FakeRunner(),
            options=WorkerOptions(
                provider_endpoint=f"http://127.0.0.1:{provider.server_port}/v1",
                provider_model="mock",
                checkpoint_dir=str(tmp_path),
            ),
        )
        claimed = worker2.poll_once()

        assert claimed == []
        assert MockControlHandler.state["status"] == "succeeded"
    finally:
        control.shutdown()
        provider.shutdown()
        control.server_close()
        provider.server_close()
        control_thread.join(timeout=5)
        provider_thread.join(timeout=5)


def test_worker_buffers_events_while_control_is_unavailable() -> None:
    MockControlHandler.state = {
        "run_id": "run_worker_test",
        "mission_id": "mission_1",
        "target_id": "target_1",
        "status": "queued",
        "events": [],
        "finding": None,
        "provider_endpoint": "http://provider.test/v1",
    }
    MockControlHandler.reject_events = True
    MockProviderHandler.block_request = None
    MockProviderHandler.release = None
    MockProviderHandler.request_seen = None
    control = ThreadingHTTPServer(("127.0.0.1", 0), MockControlHandler)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), MockProviderHandler)
    control_thread = threading.Thread(target=control.serve_forever, daemon=True)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    control_thread.start()
    provider_thread.start()
    _wait_ready(control)
    _wait_ready(provider)
    try:
        client = ControlPlaneClient(
            f"http://127.0.0.1:{control.server_port}"
        )
        worker = ControlPlaneRunWorker(
            client,
            runner_factory=lambda: FakeRunner(),
            options=WorkerOptions(
                provider_endpoint=f"http://127.0.0.1:{provider.server_port}/v1",
                provider_model="mock",
            ),
        )
        MockControlHandler.state["provider_endpoint"] = (
            f"http://127.0.0.1:{provider.server_port}/v1"
        )

        claimed = worker.poll_once()

        assert claimed == ["run_worker_test"]
        assert MockControlHandler.state["status"] == "running"
        assert MockControlHandler.state["events"] == []

        MockControlHandler.reject_events = False
        deadline = time.time() + 30
        while (
            time.time() < deadline
            and MockControlHandler.state["status"] != "succeeded"
        ):
            worker.poll_once()
            time.sleep(0.2)

        assert MockControlHandler.state["status"] == "succeeded"
        event_types = [
            event["event_type"]
            for event in MockControlHandler.state["events"]
        ]
        assert "model.turn.started" in event_types
    finally:
        control.shutdown()
        provider.shutdown()
        control.server_close()
        provider.server_close()
        control_thread.join(timeout=5)
        provider_thread.join(timeout=5)


def test_event_spool_marks_overflow() -> None:
    class FailingClient:
        def post_event(self, run_id, event_id, event_type, payload):
            raise WorkerControlError("control plane unavailable")

    sink = ControlPlaneEventSink(
        FailingClient(),
        "run_worker_test",
        spool_limit=2,
    )
    for index in range(2):
        sink.emit(
            stream_id="run_worker_test",
            run_id="run_worker_test",
            event_type="model.delta",
            actor="agent-worker",
            payload={"text": str(index)},
        )

    with pytest.raises(SpoolOverflow):
        sink.emit(
            stream_id="run_worker_test",
            run_id="run_worker_test",
            event_type="model.delta",
            actor="agent-worker",
            payload={"text": "overflow"},
        )

    assert sink.overflow is True


def test_control_client_retries_transient_transport_errors(
    monkeypatch,
) -> None:
    import httpx

    calls = 0

    def flaky_request(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection refused", request=None)

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"ok": True}

        return FakeResponse()

    monkeypatch.setattr(
        "services.agent_runtime.control_worker.httpx.request",
        flaky_request,
    )
    client = ControlPlaneClient("http://127.0.0.1:1")

    payload = client.post(
        "/api/v1/runs/run_1/findings",
        {"vuln_category": "SQLi"},
    )

    assert payload == {"ok": True}
    assert calls == 2


def test_worker_persists_observations_to_project_memory(tmp_path) -> None:
    worker = ControlPlaneRunWorker(
        ControlPlaneClient("http://127.0.0.1:1"),
        options=WorkerOptions(memory_db=str(tmp_path / "memory.db")),
    )
    spec = AgentRunSpec(
        run_id="run_memory_test",
        mission_id="mission_1",
        target_ref="https://lab.example.test",
        behavior_snapshot="behavior_memory_test",
        allowed_targets=("https://lab.example.test",),
        allowed_tools=("proxy.list", "shell.probe"),
        mission="record observations",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    active = _ActiveRun(
        run_id="run_memory_test",
        agent_spec=spec,
        kernel=None,
        runner=None,
        project_id="project_1",
    )
    observations = [
        {
            "endpoint": "/admin",
            "tool": "proxy.list",
            "stdout": "HTTP 200",
            "artifact_refs": ["artifact://run_memory_test/stdout"],
        },
        {
            "tool": "shell.probe",
            "stdout": "probe ok",
        },
        {
            "endpoint": "/replay",
            "tool": "web.replay",
            "stdout": "mismatch",
            "replay_proof": {"matched": False, "replayed_status": 500},
        },
    ]

    worker._persist_run_memory(active, observations)

    memory = SqliteProjectMemory(tmp_path / "memory.db", "project_1")
    views = memory.projection()
    assert len(views) == 3
    by_predicate = {
        view.fact.predicate: view.fact
        for view in views
    }
    assert by_predicate["observed:proxy.list"].source_refs == (
        "artifact://run_memory_test/stdout",
    )
    assert by_predicate["observed:shell.probe"].source_refs == ()
    replay_statuses = {
        view.status
        for view in views
        if view.fact.subject == "/replay"
    }
    assert replay_statuses == {"stale"}


def test_worker_budget_exhaustion_pauses_run(tmp_path) -> None:
    MockControlHandler.state = {
        "run_id": "run_worker_test",
        "mission_id": "mission_1",
        "target_id": "target_1",
        "status": "queued",
        "events": [],
        "finding": None,
        "provider_endpoint": "http://provider.test/v1",
        "mission_spec": {
            "allowed_tools": ["shell.probe", "run.finish"],
            "wall_clock_seconds": 0,
        },
    }
    MockControlHandler.reject_events = False
    MockProviderHandler.block_request = None
    MockProviderHandler.release = None
    control = ThreadingHTTPServer(("127.0.0.1", 0), MockControlHandler)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), MockProviderHandler)
    control_thread = threading.Thread(target=control.serve_forever, daemon=True)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    control_thread.start()
    provider_thread.start()
    _wait_ready(control)
    _wait_ready(provider)
    try:
        client = ControlPlaneClient(
            f"http://127.0.0.1:{control.server_port}"
        )
        worker = ControlPlaneRunWorker(
            client,
            runner_factory=lambda: FakeRunner(),
            options=WorkerOptions(
                provider_endpoint=f"http://127.0.0.1:{provider.server_port}/v1",
                provider_model="mock",
                memory_db=str(tmp_path / "memory.db"),
            ),
        )
        MockControlHandler.state["provider_endpoint"] = (
            f"http://127.0.0.1:{provider.server_port}/v1"
        )

        claimed = worker.poll_once()

        assert claimed == ["run_worker_test"]
        assert MockControlHandler.state["status"] == "paused"
        event_types = [
            event["event_type"]
            for event in MockControlHandler.state["events"]
        ]
        assert "run.budget_exhausted" in event_types
    finally:
        control.shutdown()
        provider.shutdown()
        control.server_close()
        provider.server_close()
        control_thread.join(timeout=5)
        provider_thread.join(timeout=5)


def test_worker_multi_role_mode_runs_real_provider_role_graph() -> None:
    MockControlHandler.state = {
        "run_id": "run_worker_test",
        "mission_id": "mission_1",
        "target_id": "target_1",
        "status": "queued",
        "events": [],
        "finding": None,
        "provider_endpoint": "http://provider.test/v1",
        "mission_spec": {
            "mode": "multi_role",
            "compare": True,
            "allowed_tools": ["shell.probe", "run.finish"],
            "roles": [
                {
                    "role_id": "discovery",
                    "profile": "hypothesis",
                    "node_type": "loop",
                    "allowed_tools": ["shell.probe"],
                    "oracle_ref": "coverage_oracle",
                    "budget": {
                        "hypotheses": ["https://lab.example.test"]
                    },
                },
                {
                    "role_id": "verifier",
                    "profile": "hypothesis",
                    "node_type": "loop",
                    "allowed_tools": ["shell.probe"],
                    "oracle_ref": "verifier_oracle",
                    "budget": {
                        "hypotheses": ["https://lab.example.test"]
                    },
                },
                {
                    "role_id": "reporter",
                    "node_type": "aggregate",
                    "profile": "reporter",
                },
            ],
        },
    }
    MockControlHandler.reject_events = False
    MockProviderHandler.block_request = None
    MockProviderHandler.release = None
    control = ThreadingHTTPServer(("127.0.0.1", 0), MockControlHandler)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), MockProviderHandler)
    control_thread = threading.Thread(target=control.serve_forever, daemon=True)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    control_thread.start()
    provider_thread.start()
    _wait_ready(control)
    _wait_ready(provider)
    try:
        client = ControlPlaneClient(
            f"http://127.0.0.1:{control.server_port}"
        )
        worker = ControlPlaneRunWorker(
            client,
            runner_factory=lambda: FakeRunner(),
            options=WorkerOptions(
                provider_endpoint=f"http://127.0.0.1:{provider.server_port}/v1",
                provider_model="mock",
            ),
        )
        MockControlHandler.state["provider_endpoint"] = (
            f"http://127.0.0.1:{provider.server_port}/v1"
        )

        claimed = worker.poll_once()

        assert claimed == ["run_worker_test"]
        assert MockControlHandler.state["status"] == "succeeded"
        event_types = [
            event["event_type"]
            for event in MockControlHandler.state["events"]
        ]
        assert "graph.started" in event_types
        assert "graph.completed" in event_types
        assert "graph.recommendation" in event_types
        assert event_types.count("graph.node.completed") == 3
        assert event_types.count("graph.handoff") == 2
        recommendation = next(
            event
            for event in MockControlHandler.state["events"]
            if event["event_type"] == "graph.recommendation"
        )
        assert recommendation["payload"]["recommendation"] == "graph"
        started = next(
            event
            for event in MockControlHandler.state["events"]
            if event["event_type"] == "graph.started"
        )
        assert started["payload"]["roles"][0]["role_id"] == "discovery"
        assert "budget" in started["payload"]["roles"][0]
    finally:
        control.shutdown()
        provider.shutdown()
        control.server_close()
        provider.server_close()
        control_thread.join(timeout=5)
        provider_thread.join(timeout=5)


def test_worker_multi_role_uses_mission_provider_config(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_backend_init(self, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after backend construction")

    monkeypatch.setattr(
        "services.agent_runtime.control_worker.OpenAICompatibleTurnBackend.__init__",
        fake_backend_init,
    )
    worker = ControlPlaneRunWorker(
        ControlPlaneClient("http://127.0.0.1:1"),
        options=WorkerOptions(memory_db=":memory:"),
    )
    spec = AgentRunSpec(
        run_id="run_multi_provider",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("web.nikto.scan", "run.finish"),
        mission="scanner verify",
        provider_model="fallback-model",
        provider_endpoint="http://fallback.test/v1",
    )
    events = InMemoryEventSink()
    mission_spec = {
        "mode": "multi_role",
        "role_template": "scanner_verify",
        "provider": {
            "endpoint": "https://api.deepseek.com/v1",
            "model": "deepseek-v4-flash",
            "api_key_ref": "env:DEEPSEEK_API_KEY",
        },
    }

    with pytest.raises(RuntimeError, match="stop after backend construction"):
        worker._run_multi_role_mode(
            "run_multi_provider",
            spec,
            mission_spec,
            events,
            provider=mission_spec["provider"],
        )

    assert captured["api_key"] == "env:DEEPSEEK_API_KEY"
    assert captured["base_url"] == "https://api.deepseek.com/v1"
    assert captured["model"] == "deepseek-v4-flash"


def test_worker_multi_role_falls_back_to_default_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_backend_init(self, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after backend construction")

    monkeypatch.setattr(
        "services.agent_runtime.control_worker.OpenAICompatibleTurnBackend.__init__",
        fake_backend_init,
    )

    class StubClient:
        def get_provider_default(self) -> dict:
            return {
                "provider_id": "deepseek",
                "endpoint": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-flash",
                "api_key_ref": "env:DEEPSEEK_API_KEY",
            }

    worker = ControlPlaneRunWorker(
        StubClient(),
        options=WorkerOptions(memory_db=":memory:"),
    )
    spec = AgentRunSpec(
        run_id="run_multi_default",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("web.nikto.scan", "run.finish"),
        mission="scanner verify",
        provider_model="fallback-model",
        provider_endpoint="http://fallback.test/v1",
    )
    events = InMemoryEventSink()
    mission_spec = {
        "mode": "multi_role",
        "role_template": "scanner_verify",
    }

    with pytest.raises(RuntimeError, match="stop after backend construction"):
        worker._run_multi_role_mode(
            "run_multi_default",
            spec,
            mission_spec,
            events,
            provider={},
        )

    assert captured["api_key"] == "env:DEEPSEEK_API_KEY"
    assert captured["base_url"] == "https://api.deepseek.com/v1"
    assert captured["model"] == "deepseek-v4-flash"


def test_worker_multi_role_succeeds_with_verified_finding(monkeypatch) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.finished: list[tuple[str, str]] = []

        def finish(self, run_id, outcome, idempotency_key, **kwargs):
            self.finished.append((run_id, outcome))

        def post_event(self, *args, **kwargs):
            return None

        def get_human_gates(self, run_id):
            return {"resolved": {}}

    client = StubClient()
    worker = ControlPlaneRunWorker(
        client,
        options=WorkerOptions(memory_db=":memory:"),
    )
    spec = AgentRunSpec(
        run_id="run_multi_verified",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("nmap.scan", "run.finish"),
        mission="nmap recon",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    events = InMemoryEventSink()
    mission_spec = {
        "mode": "multi_role",
        "role_template": "scanner_verify",
        "scanner_tools": ["nmap.scan"],
        "required_categories": ["Exposure"],
        "min_severity": "low",
    }

    class StubGraph:
        def run(self):
            result = type(
                "StubResult",
                (),
                {
                    "waiting": False,
                    "waiting_nodes": (),
                    "node_statuses": (
                        ("scanner", "inconclusive"),
                        ("verifier", "pending"),
                        ("reporter", "pending"),
                    ),
                    "handoffs": (),
                    "facts": (
                        FactRecord(
                            fact_id="fact_exposure",
                            subject="compose-dvwa-1:80",
                            predicate="finding",
                            value="Exposure",
                            source_refs=("artifact://scan/1",),
                        ),
                        MemoryFactRecord(
                            fact_id="fact_handoff_scanner_verifier",
                            subject="handoff://scanner",
                            predicate="handed_to",
                            value="verifier",
                        ),
                    ),
                    "metrics": type(
                        "Metrics",
                        (),
                        {
                            "handoffs": 0,
                            "dead_letters": 0,
                            "duplicate_actions": 0,
                            "path_efficiency": 1.0,
                        },
                    )(),
                },
            )()
            return result

    monkeypatch.setattr(
        "services.agent_runtime.control_worker.RoleGraphRunner",
        lambda **kwargs: StubGraph(),
    )
    monkeypatch.setattr(
        ControlPlaneRunWorker,
        "_submit_graph_findings",
        lambda self, active, facts: [
            {
                "finding_id": "finding_1",
                "status": "verified",
                "vuln_category": "Exposure",
            }
        ],
    )

    worker._run_multi_role_mode(
        "run_multi_verified",
        spec,
        mission_spec,
        events,
        provider={},
    )

    assert client.finished == [("run_multi_verified", "succeeded")]


def test_worker_multi_role_forwards_loop_events(monkeypatch) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.finished: list[tuple[str, str]] = []

        def finish(self, run_id, outcome, idempotency_key, **kwargs):
            self.finished.append((run_id, outcome))

        def post_event(self, *args, **kwargs):
            return None

        def get_human_gates(self, run_id):
            return {"resolved": {}}

    client = StubClient()
    worker = ControlPlaneRunWorker(
        client,
        options=WorkerOptions(memory_db=":memory:"),
    )
    spec = AgentRunSpec(
        run_id="run_loop_events",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("nmap.scan", "run.finish"),
        mission="parallel recon",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    events = InMemoryEventSink()
    mission_spec = {
        "mode": "multi_role",
        "role_template": "scanner_verify",
        "scanner_tools": ["nmap.scan"],
        "required_categories": ["Exposure"],
        "min_severity": "low",
        "budget": {"parallel_tool_calls": True},
    }

    class FakeLoopRunner:
        def __init__(self, *args, **kwargs) -> None:
            self.events = (
                LoopEvent(
                    loop_id="scanner",
                    event_type="loop.action.proposed",
                    sequence=1,
                    iteration=1,
                    payload={"tool": "nmap.scan"},
                ),
            )

    class FakeGraph:
        def __init__(self, **kwargs) -> None:
            self.runner_factory = kwargs["runner_factory"]

        def run(self):
            self.runner_factory(
                LoopSpec(
                    loop_id="scanner",
                    profile="hypothesis",
                    budget={"parallel_tool_calls": True},
                )
            )
            return type(
                "StubResult",
                (),
                {
                    "waiting": False,
                    "waiting_nodes": (),
                    "node_statuses": (
                        ("scanner", "succeeded"),
                        ("verifier", "succeeded"),
                        ("reporter", "succeeded"),
                    ),
                    "handoffs": (),
                    "facts": (
                        FactRecord(
                            fact_id="fact_exposure",
                            subject="compose-dvwa-1:80",
                            predicate="finding",
                            value="Exposure",
                            source_refs=("artifact://scan/1",),
                        ),
                    ),
                    "metrics": type(
                        "Metrics",
                        (),
                        {
                            "handoffs": 0,
                            "dead_letters": 0,
                            "duplicate_actions": 0,
                            "path_efficiency": 1.0,
                        },
                    )(),
                },
            )()

    monkeypatch.setattr(
        "services.agent_runtime.control_worker.RoleGraphRunner",
        FakeGraph,
    )
    monkeypatch.setattr(
        "services.agent_runtime.control_worker.LoopRunner",
        FakeLoopRunner,
    )
    monkeypatch.setattr(
        ControlPlaneRunWorker,
        "_submit_graph_findings",
        lambda self, active, facts: [],
    )

    worker._run_multi_role_mode(
        "run_loop_events",
        spec,
        mission_spec,
        events,
        provider={},
    )

    loop_events = [
        event
        for event in events.replay("run_loop_events")
        if event.event_type.startswith("loop.")
    ]
    assert len(loop_events) == 1
    assert loop_events[0].payload["tool"] == "nmap.scan"
    assert loop_events[0].payload["iteration"] == 1
    assert loop_events[0].payload["loop_id"] == "scanner"


def test_worker_multi_role_dispatches_to_remote_node(monkeypatch) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.dispatched: list[tuple[str, str, dict]] = []
            self.submitted: list[dict] = []
            self.finished: list[tuple[str, str]] = []
            self.result_calls = 0

        def post_remote_dispatch(self, node_id, task_ref, payload):
            self.dispatched.append((node_id, task_ref, payload))
            return {
                "lease": {"lease_id": "lease_remote"},
                "dispatch": {"node_id": node_id, "task_ref": task_ref},
            }

        def get_remote_results(self, node_id):
            self.result_calls += 1
            if self.result_calls == 1:
                return []
            return [
                {
                    "result_id": "result_remote",
                    "node_id": node_id,
                    "task_ref": "run_remote_dispatch",
                    "status": "completed",
                    "signature": "sig-remote",
                    "payload": {
                        "stdout": "REMOTE_OK",
                        "stderr": "",
                    },
                }
            ]

        def submit_finding(self, run_id, **kwargs):
            finding = {
                "finding_id": "finding_remote",
                "run_id": run_id,
                **kwargs,
            }
            self.submitted.append(finding)
            return finding

        def support_finding(self, finding_id):
            return {"finding_id": finding_id, "status": "supported"}

        def verify_finding(self, finding_id, oracle="verified"):
            return {"finding_id": finding_id, "status": "verified"}

        def finish(self, run_id, outcome, idempotency_key, **kwargs):
            self.finished.append((run_id, outcome))
            return None

        def post_event(self, *args, **kwargs):
            return None

        def get_human_gates(self, run_id):
            return {"resolved": {}}

    client = StubClient()
    worker = ControlPlaneRunWorker(
        client,
        options=WorkerOptions(memory_db=":memory:"),
    )
    spec = AgentRunSpec(
        run_id="run_remote_dispatch",
        mission_id="mission_1",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_1",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("nmap.scan", "run.finish"),
        mission="remote recon",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    events = InMemoryEventSink()
    mission_spec = {
        "mode": "multi_role",
        "role_template": "scanner_verify",
        "scanner_tools": ["nmap.scan"],
        "expected_finding_marker": "REMOTE_OK",
        "vuln_category": "RemoteEvidence",
        "execution": {
            "node_id": "agent-node-1",
            "wait_seconds": 0.01,
            "poll_interval": 0.01,
        },
    }

    class StubGraph:
        def run(self):
            result = type(
                "StubResult",
                (),
                {
                    "waiting": False,
                    "waiting_nodes": (),
                    "node_statuses": (
                        ("scanner", "inconclusive"),
                        ("verifier", "pending"),
                        ("reporter", "pending"),
                    ),
                    "handoffs": (),
                    "facts": (),
                    "metrics": type(
                        "Metrics",
                        (),
                        {
                            "handoffs": 0,
                            "dead_letters": 0,
                            "duplicate_actions": 0,
                            "path_efficiency": 0.0,
                        },
                    )(),
                },
            )()
            return result

    monkeypatch.setattr(
        "services.agent_runtime.control_worker.RoleGraphRunner",
        lambda **kwargs: StubGraph(),
    )
    monkeypatch.setattr(
        ControlPlaneRunWorker,
        "_submit_graph_findings",
        lambda self, active, facts: [],
    )

    worker._run_multi_role_mode(
        "run_remote_dispatch",
        spec,
        mission_spec,
        events,
        provider={},
    )

    assert client.dispatched[0][0] == "agent-node-1"
    assert client.dispatched[0][1] == "run_remote_dispatch"
    assert client.dispatched[0][2]["tool"] == "nmap.scan"
    assert client.dispatched[0][2]["args"]["target"] == (
        "http://compose-dvwa-1:80"
    )
    assert (
        "run.remote_dispatched"
        in [event.event_type for event in events.replay("run_remote_dispatch")]
    )
    assert (
        "run.remote_result_received"
        in [event.event_type for event in events.replay("run_remote_dispatch")]
    )
    assert (
        "finding.remote.verified"
        in [event.event_type for event in events.replay("run_remote_dispatch")]
    )
    assert client.submitted[0]["vuln_category"] == "RemoteEvidence"
    assert client.finished[-1][1] == "succeeded"


def test_worker_remote_duplicate_finding_is_treated_as_verified(
    monkeypatch,
) -> None:
    class DuplicateClient:
        def __init__(self) -> None:
            self.finished: list[tuple[str, str]] = []

        def post_remote_dispatch(self, node_id, task_ref, payload):
            return {"lease": {"lease_id": "lease_dup"}}

        def get_remote_results(self, node_id):
            return [
                {
                    "result_id": "result_dup",
                    "node_id": node_id,
                    "task_ref": "run_remote_dup",
                    "status": "completed",
                    "payload": {"stdout": "REMOTE_DUP"},
                }
            ]

        def submit_finding(self, run_id, **kwargs):
            return {
                "finding_id": "finding_dup",
                "run_id": run_id,
                **kwargs,
            }

        def support_finding(self, finding_id):
            raise RuntimeError("duplicate")

        def get_finding(self, finding_id):
            return {"finding_id": finding_id, "status": "duplicate"}

        def finish(self, run_id, outcome, idempotency_key, **kwargs):
            self.finished.append((run_id, outcome))

        def post_event(self, *args, **kwargs):
            return None

        def get_human_gates(self, run_id):
            return {"resolved": {}}

    client = DuplicateClient()
    worker = ControlPlaneRunWorker(
        client,
        options=WorkerOptions(memory_db=":memory:"),
    )
    spec = AgentRunSpec(
        run_id="run_remote_dup",
        mission_id="mission_dup",
        target_ref="http://compose-dvwa-1:80",
        behavior_snapshot="behavior_dup",
        allowed_targets=("http://compose-dvwa-1:80",),
        allowed_tools=("nmap.scan", "run.finish"),
        mission="remote recon",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    events = InMemoryEventSink()
    mission_spec = {
        "mode": "multi_role",
        "role_template": "scanner_verify",
        "scanner_tools": ["nmap.scan"],
        "expected_finding_marker": "REMOTE_DUP",
        "vuln_category": "RemoteDup",
        "execution": {
            "node_id": "agent-node-dup",
            "wait_seconds": 0.01,
            "poll_interval": 0.01,
        },
    }

    class StubGraph:
        def run(self):
            result = type(
                "StubResult",
                (),
                {
                    "waiting": False,
                    "waiting_nodes": (),
                    "node_statuses": (("scanner", "inconclusive"),),
                    "handoffs": (),
                    "facts": (),
                    "metrics": type(
                        "Metrics",
                        (),
                        {
                            "handoffs": 0,
                            "dead_letters": 0,
                            "duplicate_actions": 0,
                            "path_efficiency": 0.0,
                        },
                    )(),
                },
            )()
            return result

    monkeypatch.setattr(
        "services.agent_runtime.control_worker.RoleGraphRunner",
        lambda **kwargs: StubGraph(),
    )
    monkeypatch.setattr(
        ControlPlaneRunWorker,
        "_submit_graph_findings",
        lambda self, active, facts: [],
    )

    worker._run_multi_role_mode(
        "run_remote_dup",
        spec,
        mission_spec,
        events,
        provider={},
    )

    event_types = [
        event.event_type for event in events.replay("run_remote_dup")
    ]
    assert "finding.remote.duplicate" in event_types
    assert "finding.remote.failed" not in event_types
    assert client.finished[-1][1] == "succeeded"


def test_worker_submits_connector_findings_as_candidates() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.submitted: list[dict] = []
            self.supported: list[str] = []

        def submit_finding(self, run_id, **hint):
            finding = {
                "finding_id": f"finding_{len(self.submitted)}",
                "run_id": run_id,
                **hint,
            }
            self.submitted.append(finding)
            return finding

        def support_finding(self, finding_id):
            self.supported.append(finding_id)

    client = StubClient()
    worker = ControlPlaneRunWorker(
        client,
        options=WorkerOptions(memory_db=":memory:"),
    )
    spec = AgentRunSpec(
        run_id="run_connector",
        mission_id="mission_1",
        target_ref="https://lab.example.test",
        behavior_snapshot="behavior_connector",
        allowed_targets=("https://lab.example.test",),
        allowed_tools=("zap.scan",),
        mission="connector findings",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    events = InMemoryEventSink()
    active = _ActiveRun(
        run_id="run_connector",
        agent_spec=spec,
        kernel=None,
        runner=None,
        events=events,
        project_id="project_1",
    )

    worker._submit_connector_findings(
        active,
        [
            {
                "request_id": "zap:7",
                "endpoint": "https://lab.example.test/?q=x",
                "vuln_category": "XSS",
                "risk": "High",
            }
        ],
    )

    assert len(client.submitted) == 1
    assert client.submitted[0]["vuln_category"] == "XSS"
    assert client.supported == ["finding_0"]
    event_types = [
        event.event_type
        for event in events.replay("run_connector")
    ]
    assert "finding.connector.candidate" in event_types


def test_worker_records_tool_environment_digest_in_harness(tmp_path) -> None:
    snapshot = {
        "builder_version": "tool-env-1",
        "digest": "env_digest_123",
        "packs": [],
    }
    (tmp_path / "tool-environment.json").write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )

    class Sink:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def emit(self, **payload) -> None:
            self.events.append(payload)

    worker = ControlPlaneRunWorker(
        ControlPlaneClient("http://127.0.0.1:1"),
        options=WorkerOptions(
            runtime_dir=str(tmp_path),
            memory_db=":memory:",
        ),
    )
    spec = AgentRunSpec(
        run_id="run_env",
        mission_id="mission_1",
        target_ref="https://lab.example.test",
        behavior_snapshot="behavior_env",
        allowed_targets=("https://lab.example.test",),
        allowed_tools=("shell.probe",),
        mission="environment digest",
        provider_model="mock",
        provider_endpoint="http://provider.test/v1",
    )
    sink = Sink()

    worker._post_harness_snapshot(
        sink,
        "run_env",
        spec,
        {"project_id": "project_1", "spec": {}},
    )

    harness = next(
        event
        for event in sink.events
        if event["event_type"] == "harness.snapshot"
    )
    assert harness["payload"]["tool_environment_digest"] == "env_digest_123"


def test_per_node_loop_context_uses_profile_query_and_skills(
    tmp_path,
) -> None:
    from services.agent_runtime.context_projector import ContextProjection
    from services.knowledge_service.projection import (
        KnowledgeView,
        SkillProjection,
    )

    class FakeProjector:
        def __init__(self) -> None:
            self.request = None

        def project(self, request):
            self.request = request
            return ContextProjection(
                node_type=request.node_type,
                target_ref=request.target_ref,
                knowledge=KnowledgeView(
                    node_type=request.node_type,
                    chunks=(),
                    omitted=(),
                ),
                retrieval=None,
                memory_views=(),
                memory_snapshot=None,
                memory_digest="",
                skills=SkillProjection(
                    node_type=request.node_type,
                    included=(),
                    omitted=(),
                ),
                mcp_included=(),
                omitted=(),
                token_estimate=0,
                rag_degraded=(),
                context_digest="",
                knowledge_query=request.knowledge_query,
                allowed_skills=request.allowed_skills,
            )

    fake = FakeProjector()
    worker = ControlPlaneRunWorker(
        client=object(),
        options=WorkerOptions(runtime_dir=str(tmp_path)),
    )
    worker._context_projector_for = lambda _mission: fake
    agent_spec = AgentRunSpec(
        run_id="run_1",
        mission_id="mission_1",
        target_ref="https://lab.example.test",
        behavior_snapshot="behavior_1",
        allowed_targets=("https://lab.example.test",),
        allowed_tools=("web.authz.test", "run.finish"),
    )
    loop_spec = LoopSpec(
        loop_id="loop_authz",
        profile="authz_matrix",
        allowed_tools=("web.authz.test",),
        knowledge_query=("authz_oracles",),
        allowed_skills=("strix-idor",),
    )

    worker._assemble_context_blocks(
        {"project_id": "project_1", "spec": {}},
        agent_spec,
        loop_spec=loop_spec,
    )
    events = InMemoryEventSink()
    worker._emit_node_projection(events, "run_1", loop_spec)
    projection_events = events.replay("run_1")

    assert fake.request.knowledge_query == "authz_oracles"
    assert fake.request.allowed_skills == ("strix-idor",)
    assert "run_1:loop_authz" in worker._assembled
    assert len(projection_events) == 1
    assert projection_events[0].payload["loop_id"] == "loop_authz"
    assert projection_events[0].payload["profile"] == "authz_matrix"


def test_worker_graph_mode_runs_single_vs_graph_comparison() -> None:
    MockControlHandler.state = {
        "run_id": "run_worker_test",
        "mission_id": "mission_1",
        "target_id": "target_1",
        "status": "queued",
        "events": [],
        "finding": None,
        "provider_endpoint": "http://provider.test/v1",
        "mission_spec": {
            "mode": "graph",
            "scenario_id": "graph_s1",
            "graph_runs": 1,
        },
    }
    MockControlHandler.reject_events = False
    MockProviderHandler.block_request = None
    MockProviderHandler.release = None
    control = ThreadingHTTPServer(("127.0.0.1", 0), MockControlHandler)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), MockProviderHandler)
    control_thread = threading.Thread(target=control.serve_forever, daemon=True)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    control_thread.start()
    provider_thread.start()
    _wait_ready(control)
    _wait_ready(provider)
    try:
        client = ControlPlaneClient(
            f"http://127.0.0.1:{control.server_port}"
        )
        worker = ControlPlaneRunWorker(
            client,
            runner_factory=lambda: FakeRunner(),
            options=WorkerOptions(),
        )
        MockControlHandler.state["provider_endpoint"] = (
            f"http://127.0.0.1:{provider.server_port}/v1"
        )

        claimed = worker.poll_once()

        assert claimed == ["run_worker_test"]
        assert MockControlHandler.state["status"] == "succeeded"
        event_types = [
            event["event_type"]
            for event in MockControlHandler.state["events"]
        ]
        assert "graph.started" in event_types
        assert "graph.recommendation" in event_types
        recommendation = next(
            event
            for event in MockControlHandler.state["events"]
            if event["event_type"] == "graph.recommendation"
        )
        assert recommendation["payload"]["recommendation"] in (
            "single",
            "graph",
        )
    finally:
        control.shutdown()
        provider.shutdown()
        control.server_close()
        provider.server_close()
        control_thread.join(timeout=5)
        provider_thread.join(timeout=5)
