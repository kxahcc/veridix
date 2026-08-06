from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class HttpRequest:
    """One replayable HTTP request in the agent trace."""

    method: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "headers": dict(self.headers),
            "body": self.body,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HttpRequest":
        return cls(
            method=str(data["method"]),
            path=str(data["path"]),
            headers={str(k): str(v) for k, v in data.get("headers", {}).items()},
            body=str(data.get("body") or ""),
            label=str(data.get("label") or ""),
        )


@dataclass(frozen=True)
class EffectContract:
    """Structured claim extracted from an agent finding + trace.

    A finding is only a security effect if it binds to the right target,
    principal, victim principal, resource, session and state. This contract
    makes those binding dimensions explicit so they can be perturbed.
    """

    claim_id: str
    vuln_category: str
    target_ref: str
    principal: str
    victim_principal: str
    resource_ref: str
    session_ref: str
    state_epoch: str
    action_sequence: tuple[HttpRequest, ...]
    claimed_effect: str
    observed_signal: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "vuln_category": self.vuln_category,
            "target_ref": self.target_ref,
            "principal": self.principal,
            "victim_principal": self.victim_principal,
            "resource_ref": self.resource_ref,
            "session_ref": self.session_ref,
            "state_epoch": self.state_epoch,
            "action_sequence": [request.to_dict() for request in self.action_sequence],
            "claimed_effect": self.claimed_effect,
            "observed_signal": self.observed_signal,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EffectContract":
        return cls(
            claim_id=str(data["claim_id"]),
            vuln_category=str(data["vuln_category"]),
            target_ref=str(data["target_ref"]),
            principal=str(data["principal"]),
            victim_principal=str(data["victim_principal"]),
            resource_ref=str(data["resource_ref"]),
            session_ref=str(data["session_ref"]),
            state_epoch=str(data["state_epoch"]),
            action_sequence=tuple(
                HttpRequest.from_dict(item) for item in data.get("action_sequence", ())
            ),
            claimed_effect=str(data["claimed_effect"]),
            observed_signal=str(data.get("observed_signal") or ""),
        )
