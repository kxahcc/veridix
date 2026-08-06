from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .contracts import ContextView, ModelEvent, ScriptItem


@dataclass
class FakeModelAdapter:
    script: list[ScriptItem]
    calls: int = field(default=0)

    def stream(self, context: ContextView) -> Iterable[ModelEvent]:
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if item.tool_call is not None:
            yield ModelEvent(
                type="model.tool_call",
                text=item.text,
                tool_call=item.tool_call,
            )
        if item.finish:
            yield ModelEvent(type="model.finish", text=item.text)
        elif item.tool_call is None:
            yield ModelEvent(type="model.delta", text=item.text)
