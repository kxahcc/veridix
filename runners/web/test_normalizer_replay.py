from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runners.web.models import WebObservation
from runners.web.normalizer import (
    build_endpoint_model,
    classify_auth_state,
    normalize_endpoint,
    normalize_graphql_endpoint,
    normalize_observation,
    normalize_ws_channel,
    parse_graphql_request,
)
from runners.web.replay import ReplayEngine
import pytest


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/profile"):
            role = "user"
            if "role=admin" in self.path:
                role = "admin"
            body = json.dumps({"user": role, "token": "secret-value"})
            self._send(200, body)
        elif self.path == "/health":
            self._send(200, json.dumps({"status": "ok"}))
        else:
            self._send(404, json.dumps({"error": "not_found"}))

    def _send(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # pragma: no cover - test noise
        return


def test_normalization_and_auth_classification() -> None:
    assert normalize_endpoint("get", "https://lab.example.test/admin?x=1") == (
        "GET lab.example.test/admin"
    )
    assert classify_auth_state({"Cookie": "session=abc"}) == "authenticated"
    assert classify_auth_state({"User-Agent": "fixture"}) == "anonymous"


def test_endpoint_model_builds_from_observations() -> None:
    observations = (
        WebObservation(
            request_id="req_1",
            web_session_id="w",
            proxy_session_id="p",
            method="GET",
            url="https://lab.example.test/login",
            endpoint="GET lab.example.test/login",
            status_code=200,
            request_headers={},
            response_headers={},
        ),
        WebObservation(
            request_id="req_2",
            web_session_id="w",
            proxy_session_id="p",
            method="GET",
            url="https://lab.example.test/profile",
            endpoint="GET lab.example.test/profile",
            status_code=200,
            request_headers={"Cookie": "session=abc"},
            response_headers={},
        ),
    )

    model = build_endpoint_model(observations)

    assert model.endpoints == (
        "GET lab.example.test/login",
        "GET lab.example.test/profile",
    )
    assert model.auth_states == ("anonymous", "authenticated")


def test_parse_graphql_request_extracts_operation() -> None:
    parsed = parse_graphql_request(
        json.dumps(
            {
                "operationName": "GetUser",
                "query": "query GetUser($id: ID!) { user(id: $id) { id } }",
                "variables": {"id": "1"},
            }
        )
    )

    assert parsed["operation"] == "GetUser"
    assert "user(id: $id)" in parsed["query"]
    assert parsed["variables"] == {"id": "1"}


def test_normalize_graphql_endpoint_and_ws_channel() -> None:
    endpoint = normalize_graphql_endpoint(
        "POST",
        "https://lab.example.test/graphql",
        "GetUser",
    )
    channel = normalize_ws_channel(
        "wss://lab.example.test/events"
    )

    assert endpoint.endswith("/graphql#GetUser")
    assert channel == "WSS lab.example.test/events"


def test_normalize_observation_infers_graphql_protocol() -> None:
    observation = WebObservation(
        request_id="req_gql",
        web_session_id="ws",
        proxy_session_id="proxy",
        method="POST",
        url="https://lab.example.test/graphql",
        endpoint="/graphql",
        status_code=200,
        request_headers={},
        response_headers={},
        request_body=json.dumps(
            {
                "operationName": "GetUser",
                "query": "query GetUser { user { id } }",
                "variables": {},
            }
        ),
    )

    normalized = normalize_observation(observation)

    assert normalized.protocol == "graphql"
    assert normalized.graphql_operation == "GetUser"
    assert "query GetUser" in normalized.graphql_query


def test_web_observation_from_dict_is_backward_compatible() -> None:
    payload = {
        "request_id": "req_old",
        "web_session_id": "ws",
        "proxy_session_id": "proxy",
        "method": "GET",
        "url": "https://lab.example.test/",
        "endpoint": "GET lab.example.test/",
        "status_code": 200,
        "request_headers": {},
        "response_headers": {},
    }

    observation = WebObservation.from_dict(payload)

    assert observation.protocol == "http"
    assert observation.graphql_operation == ""


def test_replay_engine_diff_and_proof() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        observation = WebObservation(
            request_id="req_profile",
            web_session_id="w",
            proxy_session_id="p",
            method="GET",
            url=f"{base}/profile?role=user",
            endpoint=f"GET 127.0.0.1:{server.server_port}/profile",
            status_code=200,
            request_headers={},
            response_headers={},
            response_body=json.dumps({"user": "user", "token": "secret-value"}),
        )
        engine = ReplayEngine()

        baseline = engine.baseline(observation, base)
        method, mutated_url, headers, body = engine.mutate(
            observation,
            param="role",
            value="admin",
        )
        mutated = engine.send(method, mutated_url, headers, body)
        diff = engine.diff(
            observation.endpoint,
            baseline,
            mutated,
            {"role": "admin"},
        )
        proof = engine.replay_proof(observation)

        assert diff.changed is True
        assert diff.mutation == {"role": "admin"}
        assert proof.request_id == "req_profile"
        assert proof.request_fingerprint
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_mutation_presets_cover_multiple_vuln_types() -> None:
    observation = WebObservation(
        request_id="req_preset",
        web_session_id="w",
        proxy_session_id="p",
        method="GET",
        url="https://lab.example.test/search?q=term&role=user&id=1",
        endpoint="GET lab.example.test/search",
        status_code=200,
        request_headers={},
        response_headers={},
    )
    engine = ReplayEngine()

    idor = engine.mutate_preset(observation, "idor_role")
    xss = engine.mutate_preset(observation, "xss_reflected")
    sqli = engine.mutate_preset(observation, "sqli_boolean")
    redirect = engine.mutate_preset(observation, "open_redirect")
    traversal = engine.mutate_preset(observation, "path_traversal")
    injection = engine.mutate_preset(observation, "command_injection")

    assert "role=admin" in idor[1]
    assert "alert%281%29" in xss[1]
    assert "1+OR+1%3D1" in sqli[1]
    assert "evil.example" in redirect[1]
    assert "..%2F..%2Fetc%2Fpasswd" in traversal[1]
    assert "%3Bid" in injection[1]
    assert "xss_reflected" in ReplayEngine.presets()

    with pytest.raises(ValueError, match="unknown mutation preset"):
        engine.mutate_preset(observation, "not_a_preset")
