from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .v2_import import apply_v2_migration, rollback_v2_migration


def main() -> int:
    parser = argparse.ArgumentParser(description="migrate a V2 snapshot")
    parser.add_argument("--snapshot", required=True, help="V2 JSON snapshot")
    parser.add_argument("--db", required=True, help="target SQLite path")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--license-recorded",
        action="store_true",
        help="confirm the V2 license is recorded in the reuse ledger",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="roll back the named migration",
    )
    parser.add_argument("--migration-id", default=None)
    args = parser.parse_args()

    data = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    if args.rollback:
        if not args.migration_id:
            parser.error("--migration-id is required with --rollback")
        rollback_v2_migration(
            db_path=args.db,
            migration_id=args.migration_id,
        )
        print(json.dumps({"rolled_back": args.migration_id}))
        return 0

    record = apply_v2_migration(
        data,
        db_path=args.db,
        license_recorded=args.license_recorded,
        source_commit=args.source_commit,
    )
    print(
        json.dumps(
            {
                "migration_id": record.id,
                "version": record.version,
                "description": record.description,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
