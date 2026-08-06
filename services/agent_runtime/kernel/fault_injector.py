from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from .contracts import LoopToolResult


@dataclass(frozen=True)
class FaultSpec:
    tool: str
    fail_first_n: int = 1
    error: str = "simulated tool failure"
    error_category: str = "tool_invalid"
    retryable: bool = False


class FaultInjector:
    """Deterministic tool fault injection for self-healing gates.

    A mission can declare ``fault_injection`` in its spec:

    .. code-block:: json

        {
          "tool": "nmap.scan",
          "fail_first_n": 1,
          "error": "simulated connection timeout",
          "error_category": "tool_invalid"
        }

    The first ``fail_first_n`` calls for that tool fail with the declared
    error; later calls pass through. The agent must use the failure
    feedback to change arguments, switch tools or retry successfully.
    """

    def __init__(self, specs: tuple[FaultSpec, ...]) -> None:
        if not specs:
            raise ValueError("FaultInjector requires at least one spec")
        self._specs = {
            spec.tool: spec for spec in specs
        }
        self._lock = threading.Lock()
        self._calls: dict[str, int] = {}

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "FaultInjector | None":
        if not config:
            return None
        raw_specs = config.get("tools") or (
            [config] if config.get("tool") else []
        )
        if not raw_specs:
            return None
        specs: list[FaultSpec] = []
        for item in raw_specs:
            tool = str(item.get("tool") or "")
            if not tool:
                continue
            specs.append(
                FaultSpec(
                    tool=tool,
                    fail_first_n=max(
                        1,
                        int(item.get("fail_first_n", 1) or 1),
                    ),
                    error=str(
                        item.get("error")
                        or "simulated tool failure"
                    ),
                    error_category=str(
                        item.get("error_category") or "tool_invalid"
                    ),
                    retryable=bool(item.get("retryable", False)),
                )
            )
        return cls(tuple(specs)) if specs else None

    def maybe_fail(
        self,
        tool_ref: str,
        arguments: dict[str, Any],
    ) -> LoopToolResult | None:
        spec = self._specs.get(tool_ref)
        if spec is None:
            return None
        with self._lock:
            calls = self._calls.get(tool_ref, 0) + 1
            self._calls[tool_ref] = calls
            if calls > spec.fail_first_n:
                return None
        return LoopToolResult(
            status="failed",
            observations=(),
            facts=(),
            evidence_refs=(),
            error=spec.error,
            retryable=spec.retryable,
            error_category=spec.error_category,
        )

    @property
    def calls(self) -> int:
        with self._lock:
            return sum(self._calls.values())
