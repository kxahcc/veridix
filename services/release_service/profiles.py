from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReleaseProfile:
    name: str
    label: str
    dependencies: tuple[str, ...]


def load_profile(path: str | Path) -> ReleaseProfile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ReleaseProfile(
        name=data["name"],
        label=data.get("label", data["name"]),
        dependencies=tuple(data.get("dependencies", ())),
    )


def load_all(directory: str | Path) -> list[ReleaseProfile]:
    return [load_profile(path) for path in sorted(Path(directory).glob("*.json"))]
