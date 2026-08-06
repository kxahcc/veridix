from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class SecretResolver:
    def __init__(self, refs_path: str | Path | None = None) -> None:
        self._refs: dict[str, Any] = {}
        if refs_path is not None and Path(refs_path).exists():
            self._refs = json.loads(Path(refs_path).read_text(encoding="utf-8"))

    def resolve(self, ref: str | None) -> str | None:
        if not ref:
            return None
        if ref.startswith("env:"):
            return os.environ.get(ref[len("env:") :])
        if ref.startswith("file:"):
            path = Path(ref[len("file:") :])
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
            return None
        entry = self._refs.get(ref)
        if isinstance(entry, dict) and entry.get("env"):
            return os.environ.get(entry["env"])
        if isinstance(entry, str):
            return os.environ.get(entry)
        return None
