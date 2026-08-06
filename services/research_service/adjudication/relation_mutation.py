from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .effect_contract import EffectContract, HttpRequest


class MutationKind(str, Enum):
    BASELINE = "baseline"
    PRINCIPAL_SWAP = "principal_swap"
    RESOURCE_SWAP = "resource_swap"
    SESSION_RESET = "session_reset"
    STATE_RESET = "state_reset"
    ORDER_CHANGE = "order_change"
    TARGET_IDENTITY = "target_identity"
    BENIGN_CONTROL = "benign_control"


@dataclass(frozen=True)
class RequestPlan:
    """A concrete request to execute as part of a differential test."""

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
    def from_request(
        cls,
        request: HttpRequest,
        *,
        label: str | None = None,
        headers: dict[str, str] | None = None,
        path: str | None = None,
        body: str | None = None,
    ) -> "RequestPlan":
        return cls(
            method=request.method,
            path=path if path is not None else request.path,
            headers=dict(headers) if headers is not None else dict(request.headers),
            body=request.body if body is None else body,
            label=label if label is not None else request.label,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RequestPlan":
        return cls(
            method=str(data["method"]),
            path=str(data["path"]),
            headers={str(k): str(v) for k, v in data.get("headers", {}).items()},
            body=str(data.get("body") or ""),
            label=str(data.get("label") or ""),
        )


@dataclass(frozen=True)
class RelationMutation:
    """A single differential test: a mutated request sequence + expectation."""

    mutation_id: str
    kind: MutationKind
    description: str
    expected_direction: str  # effect_persists | effect_vanishes | no_effect
    cost: int
    requests: tuple[RequestPlan, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutation_id": self.mutation_id,
            "kind": self.kind.value,
            "description": self.description,
            "expected_direction": self.expected_direction,
            "cost": self.cost,
            "requests": [request.to_dict() for request in self.requests],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RelationMutation":
        return cls(
            mutation_id=str(data["mutation_id"]),
            kind=MutationKind(str(data["kind"])),
            description=str(data["description"]),
            expected_direction=str(data["expected_direction"]),
            cost=int(data["cost"]),
            requests=tuple(
                RequestPlan.from_dict(item) for item in data.get("requests", ())
            ),
        )


@dataclass(frozen=True)
class DifferentialOutcome:
    """Result of executing one differential test."""

    mutation_id: str
    kind: MutationKind
    expected_direction: str
    effect_detected: bool
    signal: str = ""
    observations: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutation_id": self.mutation_id,
            "kind": self.kind.value,
            "expected_direction": self.expected_direction,
            "effect_detected": self.effect_detected,
            "signal": self.signal,
            "observations": list(self.observations),
        }


@dataclass(frozen=True)
class RelationContext:
    """Environment knowledge needed to instantiate relation mutations."""

    alternate_principals: tuple[str, ...] = ()
    alternate_resources: tuple[str, ...] = ()
    alternate_targets: tuple[str, ...] = ()
    fresh_session_id: str = "session-fresh"
    principal_header_names: tuple[str, ...] = (
        "authorization",
        "x-user",
        "x-user-id",
    )
    resource_query_param: str = "resource_id"


_EXPECTED_BY_KIND: dict[MutationKind, str] = {
    MutationKind.BASELINE: "effect_persists",
    MutationKind.PRINCIPAL_SWAP: "effect_vanishes",
    MutationKind.RESOURCE_SWAP: "effect_vanishes",
    MutationKind.SESSION_RESET: "effect_vanishes",
    MutationKind.STATE_RESET: "effect_vanishes",
    MutationKind.ORDER_CHANGE: "effect_vanishes",
    MutationKind.TARGET_IDENTITY: "effect_vanishes",
    MutationKind.BENIGN_CONTROL: "no_effect",
}


class RelationMutationGenerator:
    """Generate bounded, per-dimension differential tests from a contract."""

    def __init__(
        self,
        context: RelationContext | None = None,
        *,
        max_alternatives_per_kind: int = 2,
    ) -> None:
        self._context = context or RelationContext()
        self._max_alternatives = max_alternatives_per_kind

    def generate(
        self,
        contract: EffectContract,
        *,
        max_mutations: int = 10,
    ) -> list[RelationMutation]:
        mutations: list[RelationMutation] = []
        mutations.append(self._baseline(contract))
        for builder in (
            self._principal_swaps,
            self._resource_swaps,
            self._session_resets,
            self._state_resets,
            self._order_changes,
            self._target_identities,
            self._benign_controls,
        ):
            mutations.extend(builder(contract))
            if len(mutations) >= max_mutations:
                break
        return mutations[:max_mutations]

    def _baseline(self, contract: EffectContract) -> RelationMutation:
        requests = tuple(
            RequestPlan.from_request(req, label="baseline")
            for req in contract.action_sequence
        )
        return RelationMutation(
            mutation_id=f"{contract.claim_id}:baseline",
            kind=MutationKind.BASELINE,
            description="Replay original action sequence",
            expected_direction=_EXPECTED_BY_KIND[MutationKind.BASELINE],
            cost=len(requests),
            requests=requests,
        )

    def _principal_swaps(self, contract: EffectContract) -> list[RelationMutation]:
        out: list[RelationMutation] = []
        for index, alt in enumerate(
            self._context.alternate_principals[: self._max_alternatives]
        ):
            requests = []
            for req in contract.action_sequence:
                headers = dict(req.headers)
                for name in self._context.principal_header_names:
                    if name in headers:
                        headers[name] = f"principal:{alt}"
                requests.append(
                    RequestPlan.from_request(
                        req,
                        label=f"principal_swap_{index}",
                        headers=headers,
                    )
                )
            out.append(
                RelationMutation(
                    mutation_id=f"{contract.claim_id}:principal_swap_{index}",
                    kind=MutationKind.PRINCIPAL_SWAP,
                    description=f"Execute as alternate principal {alt}",
                    expected_direction=_EXPECTED_BY_KIND[MutationKind.PRINCIPAL_SWAP],
                    cost=len(requests),
                    requests=tuple(requests),
                )
            )
        return out

    def _resource_swaps(self, contract: EffectContract) -> list[RelationMutation]:
        out: list[RelationMutation] = []
        for index, alt in enumerate(
            self._context.alternate_resources[: self._max_alternatives]
        ):
            requests = []
            for req in contract.action_sequence:
                path = req.path.replace(contract.resource_ref, alt)
                if path == req.path:
                    separator = "&" if "?" in path else "?"
                    path = f"{path}{separator}{self._context.resource_query_param}={alt}"
                requests.append(
                    RequestPlan.from_request(
                        req,
                        label=f"resource_swap_{index}",
                        path=path,
                    )
                )
            out.append(
                RelationMutation(
                    mutation_id=f"{contract.claim_id}:resource_swap_{index}",
                    kind=MutationKind.RESOURCE_SWAP,
                    description=f"Access alternate resource {alt}",
                    expected_direction=_EXPECTED_BY_KIND[MutationKind.RESOURCE_SWAP],
                    cost=len(requests),
                    requests=tuple(requests),
                )
            )
        return out

    def _session_resets(self, contract: EffectContract) -> list[RelationMutation]:
        requests = tuple(
            RequestPlan.from_request(
                req,
                label="session_reset",
                headers={
                    **dict(req.headers),
                    "cookie": self._context.fresh_session_id,
                    "x-session-reset": "1",
                },
            )
            for req in contract.action_sequence
        )
        return [
            RelationMutation(
                mutation_id=f"{contract.claim_id}:session_reset",
                kind=MutationKind.SESSION_RESET,
                description="Replay with a fresh session",
                expected_direction=_EXPECTED_BY_KIND[MutationKind.SESSION_RESET],
                cost=len(requests),
                requests=requests,
            )
        ]

    def _state_resets(self, contract: EffectContract) -> list[RelationMutation]:
        if not contract.action_sequence:
            return []
        first = contract.action_sequence[0]
        requests = (
            RequestPlan.from_request(
                first,
                label="state_reset",
                headers={**dict(first.headers), "x-state-reset": "1"},
            ),
        )
        return [
            RelationMutation(
                mutation_id=f"{contract.claim_id}:state_reset",
                kind=MutationKind.STATE_RESET,
                description="Drop prerequisite state; execute first step only",
                expected_direction=_EXPECTED_BY_KIND[MutationKind.STATE_RESET],
                cost=1,
                requests=requests,
            )
        ]

    def _order_changes(self, contract: EffectContract) -> list[RelationMutation]:
        requests = tuple(
            RequestPlan.from_request(req, label="order_change")
            for req in reversed(contract.action_sequence)
        )
        return [
            RelationMutation(
                mutation_id=f"{contract.claim_id}:order_change",
                kind=MutationKind.ORDER_CHANGE,
                description="Reverse request ordering",
                expected_direction=_EXPECTED_BY_KIND[MutationKind.ORDER_CHANGE],
                cost=len(requests),
                requests=requests,
            )
        ]

    def _target_identities(self, contract: EffectContract) -> list[RelationMutation]:
        out: list[RelationMutation] = []
        for index, alt in enumerate(
            self._context.alternate_targets[: self._max_alternatives]
        ):
            requests = tuple(
                RequestPlan.from_request(
                    req,
                    label=f"target_identity_{index}",
                    headers={
                        **dict(req.headers),
                        "host": alt,
                        "x-target-identity": alt,
                    },
                )
                for req in contract.action_sequence
            )
            out.append(
                RelationMutation(
                    mutation_id=f"{contract.claim_id}:target_identity_{index}",
                    kind=MutationKind.TARGET_IDENTITY,
                    description=f"Point at identity-distinct target {alt}",
                    expected_direction=_EXPECTED_BY_KIND[MutationKind.TARGET_IDENTITY],
                    cost=len(requests),
                    requests=requests,
                )
            )
        return out

    def _benign_controls(self, contract: EffectContract) -> list[RelationMutation]:
        requests = tuple(
            RequestPlan.from_request(
                req,
                label="benign_control",
                path=req.path.split("?", 1)[0],
                body="",
                headers={**dict(req.headers), "x-benign-control": "1"},
            )
            for req in contract.action_sequence
        )
        return [
            RelationMutation(
                mutation_id=f"{contract.claim_id}:benign_control",
                kind=MutationKind.BENIGN_CONTROL,
                description="Same flow without the exploit payload",
                expected_direction=_EXPECTED_BY_KIND[MutationKind.BENIGN_CONTROL],
                cost=len(requests),
                requests=requests,
            )
        ]
