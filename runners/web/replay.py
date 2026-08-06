from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from dataclasses import dataclass

import httpx

from .models import (
    DiffResult,
    ReplayProof,
    ResponseSnapshot,
    WebObservation,
)


@dataclass(frozen=True)
class MutationPreset:
    name: str
    param: str
    value: str


MUTATION_PRESETS = (
    MutationPreset("idor_role", "role", "admin"),
    MutationPreset("xss_reflected", "q", "<script>alert(1)</script>"),
    MutationPreset("sqli_boolean", "id", "1 OR 1=1"),
    MutationPreset("open_redirect", "next", "https://evil.example"),
    MutationPreset("path_traversal", "file", "../../etc/passwd"),
    MutationPreset("command_injection", "host", ";id"),
)


class ReplayEngine:
    def __init__(self, *, timeout: float = 5.0) -> None:
        self._timeout = timeout

    def baseline(self, observation: WebObservation, base_url: str) -> ResponseSnapshot:
        return self.send(
            observation.method,
            observation.url,
            observation.request_headers,
            observation.request_body,
        )

    def mutate(
        self,
        observation: WebObservation,
        *,
        param: str,
        value: str,
    ) -> tuple[str, str, dict[str, str], str | None]:
        parsed = urlparse(observation.url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query[param] = value
        mutated = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(query),
                parsed.fragment,
            )
        )
        return observation.method, mutated, observation.request_headers, observation.request_body

    def mutate_preset(
        self,
        observation: WebObservation,
        preset_name: str,
    ) -> tuple[str, str, dict[str, str], str | None]:
        preset = next(
            (item for item in MUTATION_PRESETS if item.name == preset_name),
            None,
        )
        if preset is None:
            raise ValueError(f"unknown mutation preset {preset_name}")
        return self.mutate(
            observation,
            param=preset.param,
            value=preset.value,
        )

    @staticmethod
    def presets() -> tuple[str, ...]:
        return tuple(item.name for item in MUTATION_PRESETS)

    def diff(
        self,
        endpoint: str,
        baseline: ResponseSnapshot,
        mutated: ResponseSnapshot,
        mutation: dict[str, str],
    ) -> DiffResult:
        return DiffResult(
            endpoint=endpoint,
            baseline_status=baseline.status_code,
            baseline_digest=baseline.body_digest,
            mutated_status=mutated.status_code,
            mutated_digest=mutated.body_digest,
            changed=(
                baseline.status_code != mutated.status_code
                or baseline.body_digest != mutated.body_digest
            ),
            mutation=mutation,
        )

    def replay_proof(self, observation: WebObservation) -> ReplayProof:
        return ReplayProof(
            request_id=observation.request_id,
            request_fingerprint=observation.request_fingerprint(),
            response_fingerprint=observation.response_fingerprint(),
            replayed_status=observation.status_code,
        )

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: str | None,
    ) -> ResponseSnapshot:
        response = httpx.request(
            method,
            url,
            headers=headers,
            content=body.encode("utf-8") if body else None,
            timeout=self._timeout,
        )
        return ResponseSnapshot(
            status_code=response.status_code,
            body_digest=_body_digest(response.text),
            headers={key: value for key, value in response.headers.items()},
        )


def _body_digest(body: str) -> str:
    import hashlib

    return hashlib.sha256(body.encode("utf-8")).hexdigest()
