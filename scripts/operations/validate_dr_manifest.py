"""Validate a disaster-recovery drill manifest.

The script is intentionally small and deterministic so CI can verify that a
restore drill recorded the required PostgreSQL and object-storage evidence
without needing privileged access to production backups.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED_KEYS = {
    "postgres_backup_id",
    "object_storage_snapshot",
    "rpo_minutes",
    "rto_minutes",
    "smoke_tests_passed",
}


def validate_manifest(
    path: Path,
    *,
    max_rpo_minutes: int = 15,
    max_rto_minutes: int = 120,
) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    missing = sorted(REQUIRED_KEYS - set(payload))
    for key in missing:
        errors.append(f"missing required key: {key}")
    if missing:
        return errors
    if int(payload["rpo_minutes"]) > max_rpo_minutes:
        errors.append("RPO exceeds target")
    if int(payload["rto_minutes"]) > max_rto_minutes:
        errors.append("RTO exceeds target")
    if payload["smoke_tests_passed"] is not True:
        errors.append("restore smoke tests did not pass")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_dr_manifest.py <manifest.json>")
        return 2
    errors = validate_manifest(Path(sys.argv[1]))
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
