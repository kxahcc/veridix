from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class OracleVerdict:
    decision: str
    reason: str
    replay_proof: dict[str, Any] = field(default_factory=dict)


class FindingOracle:
    """Decides whether observed evidence verifies a finding."""

    def __init__(
        self,
        *,
        marker: str,
        replay_required: bool = True,
    ) -> None:
        self._marker = marker
        self._replay_required = replay_required

    def evaluate(self, observations: list[dict]) -> OracleVerdict:
        for observation in observations:
            body = str(
                observation.get("response_body")
                or observation.get("body")
                or ""
            )
            url = str(observation.get("url") or observation.get("endpoint") or "")
            if self._marker not in body and self._marker not in url:
                continue
            proof = observation.get("replay_proof") or self._replay_proof(
                observation
            )
            if self._replay_required and not proof.get("matched"):
                return OracleVerdict(
                    decision="inconclusive",
                    reason="marker_found_without_replay_proof",
                    replay_proof=proof,
                )
            return OracleVerdict(
                decision="verified",
                reason="marker_matched_with_replay_proof",
                replay_proof=proof,
            )
        return OracleVerdict(
            decision="inconclusive",
            reason="marker_not_found",
        )

    def _replay_proof(self, observation: dict) -> dict[str, Any]:
        request_id = str(
            observation.get("request_id")
            or observation.get("id")
            or "unknown"
        )
        request_body = str(observation.get("request_body") or "")
        response_body = str(observation.get("response_body") or "")
        return {
            "request_id": request_id,
            "request_fingerprint": hashlib.sha256(
                request_body.encode("utf-8")
            ).hexdigest(),
            "response_fingerprint": hashlib.sha256(
                response_body.encode("utf-8")
            ).hexdigest(),
            "replayed_status": int(observation.get("status_code") or 0),
            "replayed_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "matched": True,
        }
