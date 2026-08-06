from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TransportSpec:
    kind: str
    endpoint: str
    tunnel_ref: str | None = None

    def validate(self) -> None:
        if self.kind not in ("direct", "http_proxy", "socks_proxy", "ssh_tunnel"):
            raise ValueError(f"unsupported transport kind {self.kind}")
        if not self.endpoint:
            raise ValueError("transport endpoint is required")
        if self.kind in ("ssh_tunnel",) and not self.tunnel_ref:
            raise ValueError("ssh_tunnel transport requires tunnel_ref")


@dataclass(frozen=True)
class TransportBackend:
    spec: TransportSpec
    connect: Callable[..., Any] | None = None

    def validate(self) -> None:
        self.spec.validate()

    def http_proxy_url(self) -> str | None:
        if self.spec.kind == "http_proxy":
            return self.spec.endpoint
        if self.spec.kind == "socks_proxy":
            return f"socks5://{self.spec.endpoint}"
        return None

    def open(self) -> Any:
        self.validate()
        if self.connect is None:
            raise RuntimeError(
                f"{self.spec.kind} transport has no connect backend configured"
            )
        return self.connect(self.spec)


def resolve_transport(spec: TransportSpec) -> TransportBackend:
    return TransportBackend(spec=spec)
