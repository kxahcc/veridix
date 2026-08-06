from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(parts: list) -> str:
    canonical = json.dumps(parts, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class WebObservation:
    request_id: str
    web_session_id: str
    proxy_session_id: str
    method: str
    url: str
    endpoint: str
    status_code: int
    request_headers: dict[str, str]
    response_headers: dict[str, str]
    request_body: str = ""
    response_body: str = ""
    content_type: str = ""
    request_size: int = 0
    response_size: int = 0
    artifact_ref: str = ""
    redacted: bool = False
    truncated: bool = False
    protocol: str = "http"
    graphql_operation: str = ""
    graphql_query: str = ""
    graphql_variables: dict[str, Any] = field(default_factory=dict)
    ws_frame_type: str = ""
    ws_frame_data: str = ""

    def request_fingerprint(self) -> str:
        return _digest([self.method, self.url, self.request_headers, self.request_body])

    def response_fingerprint(self) -> str:
        return _digest([self.status_code, self.response_headers, self.response_body])

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "web_session_id": self.web_session_id,
            "proxy_session_id": self.proxy_session_id,
            "method": self.method,
            "url": self.url,
            "endpoint": self.endpoint,
            "status_code": self.status_code,
            "request_headers": self.request_headers,
            "response_headers": self.response_headers,
            "request_body": self.request_body,
            "response_body": self.response_body,
            "content_type": self.content_type,
            "request_size": self.request_size,
            "response_size": self.response_size,
            "artifact_ref": self.artifact_ref,
            "redacted": self.redacted,
            "truncated": self.truncated,
            "protocol": self.protocol,
            "graphql_operation": self.graphql_operation,
            "graphql_query": self.graphql_query,
            "graphql_variables": self.graphql_variables,
            "ws_frame_type": self.ws_frame_type,
            "ws_frame_data": self.ws_frame_data,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WebObservation:
        return cls(**payload)


@dataclass(frozen=True)
class EndpointModel:
    endpoints: tuple[str, ...]
    auth_states: tuple[str, ...]
    observation_count: int


@dataclass(frozen=True)
class ResponseSnapshot:
    status_code: int
    body_digest: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiffResult:
    endpoint: str
    baseline_status: int
    baseline_digest: str
    mutated_status: int
    mutated_digest: str
    changed: bool
    mutation: dict[str, str]


@dataclass(frozen=True)
class ReplayProof:
    request_id: str
    request_fingerprint: str
    response_fingerprint: str
    replayed_status: int
    replayed_at: str = field(default_factory=utc_now)
    matched: bool = False


@dataclass(frozen=True)
class CandidateFinding:
    finding_id: str
    endpoint: str
    status: str = "candidate"
    diff: DiffResult | None = None
    evidence_refs: tuple[str, ...] = ()
    replay_proof: ReplayProof | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class GoldenResult:
    endpoint_model: EndpointModel
    candidate: CandidateFinding
    observations: tuple[WebObservation, ...]
