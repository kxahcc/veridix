from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ARTIFACT_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ArtifactQuotaExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    size: int
    content_type: str
    sha256: str
    created_at: str


class ArtifactStore:
    """Content-addressed artifact files with atomic writes and recoverable GC."""

    def __init__(self, root: str | Path, *, max_bytes: int | None = None) -> None:
        self._root = Path(root)
        self._max_bytes = max_bytes

    def put(
        self,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> Artifact:
        digest = hashlib.sha256(data).hexdigest()
        target = self._path(digest)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                target.unlink()
            else:
                return Artifact(
                    artifact_id=digest,
                    size=len(data),
                    content_type=content_type,
                    sha256=digest,
                    created_at=datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                )
        self._enforce_quota(len(data))
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".{digest}.tmp"
        tmp.write_bytes(data)
        tmp.replace(target)
        return Artifact(
            artifact_id=digest,
            size=len(data),
            content_type=content_type,
            sha256=digest,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def get(self, artifact_id: str) -> bytes:
        if not ARTIFACT_ID_PATTERN.match(artifact_id):
            raise ValueError(f"invalid artifact id {artifact_id}")
        path = self._path(artifact_id)
        if not path.exists():
            raise FileNotFoundError(artifact_id)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != artifact_id:
            raise ValueError(f"artifact hash mismatch for {artifact_id}")
        return data

    def verify(self, artifact_id: str) -> bool:
        try:
            self.get(artifact_id)
            return True
        except (FileNotFoundError, ValueError):
            return False

    def delete(self, artifact_id: str) -> None:
        path = self._path(artifact_id)
        if path.exists():
            path.unlink()
            self._prune_empty_dirs()

    def gc(self, keep: set[str]) -> list[str]:
        removed: list[str] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or not ARTIFACT_ID_PATTERN.match(path.name):
                continue
            if path.name not in keep:
                path.unlink()
                removed.append(path.name)
        self._prune_empty_dirs()
        return removed

    def used_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for path in self._root.rglob("*")
            if path.is_file() and ARTIFACT_ID_PATTERN.match(path.name)
        )

    def files(self) -> list[tuple[str, Path]]:
        return [
            (path.name, path)
            for path in sorted(self._root.rglob("*"))
            if path.is_file() and ARTIFACT_ID_PATTERN.match(path.name)
        ]

    def _enforce_quota(self, incoming: int) -> None:
        if self._max_bytes is None:
            return
        if self.used_bytes() + incoming > self._max_bytes:
            raise ArtifactQuotaExceeded(
                f"artifact quota {self._max_bytes} bytes would be exceeded"
            )

    def _path(self, artifact_id: str) -> Path:
        return (
            self._root
            / "sha256"
            / artifact_id[:2]
            / artifact_id[2:4]
            / artifact_id
        )

    def _prune_empty_dirs(self) -> None:
        for path in sorted(
            (p for p in self._root.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            try:
                path.rmdir()
            except OSError:
                pass
