from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UpgradePlan:
    upgrade_ok: bool
    reasons: tuple[str, ...]


def check_upgrade_plan(
    *,
    db_path: str | Path,
    required_disk_mb: int,
    backups_writable: bool,
) -> UpgradePlan:
    reasons: list[str] = []
    db = Path(db_path)
    if not db.exists():
        reasons.append("database missing")
    free_mb = shutil.disk_usage(db.parent).free // (1024 * 1024)
    if free_mb < required_disk_mb:
        reasons.append(f"insufficient disk: {free_mb} MB free")
    if not backups_writable:
        reasons.append("backup target not writable")
    return UpgradePlan(upgrade_ok=not reasons, reasons=tuple(reasons))


def create_backup(db_path: str | Path, backup_path: str | Path) -> str:
    shutil.copy2(db_path, backup_path)
    return str(backup_path)
