from __future__ import annotations

import json
from urllib.parse import urlparse
from typing import Any

from .models import EndpointModel, WebObservation


def normalize_endpoint(method: str, url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    return f"{method.upper()} {host}{path}"


def classify_auth_state(request_headers: dict[str, str]) -> str:
    lowered = {key.lower(): value for key, value in request_headers.items()}
    if lowered.get("authorization") or lowered.get("cookie"):
        return "authenticated"
    return "anonymous"


def parse_graphql_request(body: str) -> dict[str, Any]:
    """Extract operation/query/variables from a GraphQL JSON body."""
    if not body:
        return {}
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    variables = payload.get("variables")
    return {
        "operation": str(payload.get("operationName") or ""),
        "query": str(payload.get("query") or ""),
        "variables": variables if isinstance(variables, dict) else {},
    }


def normalize_graphql_endpoint(
    method: str,
    url: str,
    operation: str,
) -> str:
    base = normalize_endpoint(method, url)
    if not operation:
        return base
    return f"{base}#{operation}"


def normalize_ws_channel(url: str) -> str:
    parsed = urlparse(url)
    scheme = "wss" if parsed.scheme in ("https", "wss") else "ws"
    return f"{scheme.upper()} {parsed.netloc}{parsed.path or '/'}"


def normalize_observation(observation) -> "WebObservation":
    """Infer protocol and protocol-specific fields for one observation."""
    protocol = observation.protocol or "http"
    if protocol == "http" and (
        "graphql" in observation.url.lower()
        or "graphql" in observation.content_type.lower()
    ):
        protocol = "graphql"
    graphql = (
        parse_graphql_request(observation.request_body)
        if protocol in ("http", "graphql")
        else {}
    )
    return observation.__class__(
        **{
            **observation.to_dict(),
            "protocol": protocol,
            "graphql_operation": str(
                graphql.get("operation") or observation.graphql_operation
            ),
            "graphql_query": str(
                graphql.get("query") or observation.graphql_query
            ),
            "graphql_variables": (
                graphql.get("variables")
                or observation.graphql_variables
                or {}
            ),
        }
    )


def build_endpoint_model(
    observations: tuple[WebObservation, ...],
) -> EndpointModel:
    endpoints = tuple(sorted({observation.endpoint for observation in observations}))
    auth_states = tuple(
        sorted(
            {
                classify_auth_state(observation.request_headers)
                for observation in observations
            }
        )
    )
    return EndpointModel(
        endpoints=endpoints,
        auth_states=auth_states,
        observation_count=len(observations),
    )
