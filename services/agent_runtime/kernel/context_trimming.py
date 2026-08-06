from __future__ import annotations

from typing import Any


def estimate_tokens(text: str) -> int:
    """Rough token estimate used for budget-driven context trimming."""
    return max(1, len(text) // 4)


def compact_observation(observation: dict[str, Any]) -> str:
    tool = str(observation.get("tool") or "tool")
    category = str(
        observation.get("vuln_category")
        or observation.get("template_id")
        or observation.get("rule_id")
        or ""
    )
    endpoint = str(observation.get("endpoint") or observation.get("url") or "")
    parts = [tool]
    if category:
        parts.append(category)
    if endpoint:
        parts.append(endpoint)
    return ":".join(parts)


def trim_observations(
    observations: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    max_context_tokens: int = 32_000,
    reserve_tokens: int = 4_000,
    keep_recent: int = 2,
) -> tuple[tuple[dict[str, Any], ...], str, tuple[dict[str, Any], ...]]:
    """Trim oldest tool observations while keeping a compact summary.

    The returned summary is rendered into provider context so the model keeps
    a compressed record of earlier evidence instead of losing it entirely.
    """
    items = list(observations)

    def _size(observation: dict[str, Any]) -> int:
        return estimate_tokens(
            str(observation.get("stdout") or "")
        ) + estimate_tokens(str(observation.get("arguments") or {}))

    budget = max(max_context_tokens - reserve_tokens, 1)
    if not items or sum(_size(item) for item in items) <= budget:
        return tuple(items), "", ()

    trimmed: list[str] = []
    removed_items: list[dict[str, Any]] = []
    while len(items) > keep_recent and sum(
        _size(item) for item in items
    ) > budget:
        removed = items.pop(0)
        removed_items.append(removed)
        trimmed.append(compact_observation(removed))

    summary = ""
    if trimmed:
        summary = (
            "Trimmed earlier observations: "
            + "; ".join(trimmed[-20:])
        )
    return tuple(items), summary, tuple(removed_items)


class BackendSummarizer:
    """LLM-based summarizer that reuses the same turn backend."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def __call__(
        self,
        observations: tuple[dict[str, Any], ...],
    ) -> str:
        from .contracts import ContextView, ModelEvent

        view = ContextView(
            mission=(
                "Summarize the earlier security testing observations below "
                "into at most 4 concise bullets. Preserve endpoints, tools, "
                "categories, evidence status and any replay proof."
            ),
            target_ref="",
            observations=observations,
            remaining_budget=0,
            context_blocks=None,
        )
        parts: list[str] = []
        for event in self._backend.stream(view):
            if (
                isinstance(event, ModelEvent)
                and event.type == "model.finish"
                and event.text
            ):
                parts.append(event.text)
        return "\n".join(parts).strip()
