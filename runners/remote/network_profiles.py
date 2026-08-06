from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteNetworkProfile:
    mode: str
    tunnel_ref: str | None = None
    deny_ranges: tuple[str, ...] = ("169.254.169.254/32",)

    def validate(self) -> None:
        if self.mode not in ("direct", "proxy", "tunnel", "none"):
            raise ValueError(f"unsupported network mode {self.mode}")
        if self.mode == "tunnel" and not self.tunnel_ref:
            raise ValueError("tunnel mode requires tunnel_ref")
