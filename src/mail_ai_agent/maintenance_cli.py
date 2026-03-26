from __future__ import annotations

import argparse
import json
from pathlib import Path

from .maintenance import maintain_sqlite, prune_drafts, rotate_audit_log


def main() -> None:
    parser = argparse.ArgumentParser(description="Maintenance utilities for AI Mail Triage")
    parser.add_argument("--audit-log", default="logs/audit.jsonl", help="Path to audit log")
    parser.add_argument("--state-db", default="data/state.sqlite", help="Path to SQLite DB")
    parser.add_argument("--draft-dir", default="drafts/pending", help="Path to local drafts")
    parser.add_argument("--rotate-audit-max-bytes", type=int, default=0, help="Rotate audit log if size exceeds this threshold")
    parser.add_argument("--audit-backup-count", type=int, default=5, help="Number of rotated audit backups to keep")
    parser.add_argument("--prune-drafts-older-than-days", type=int, default=None, help="Delete draft files older than the given number of days")
    parser.add_argument("--vacuum-db", action="store_true", help="Run SQLite integrity check, checkpoint, and vacuum")
    args = parser.parse_args()

    payload: dict[str, object] = {}

    if args.rotate_audit_max_bytes > 0:
        result = rotate_audit_log(
            Path(args.audit_log),
            max_bytes=args.rotate_audit_max_bytes,
            backup_count=args.audit_backup_count,
        )
        payload["audit_rotation"] = {
            "rotated": result.rotated,
            "archive_path": str(result.archive_path) if result.archive_path else None,
            "original_size": result.original_size,
        }

    if args.prune_drafts_older_than_days is not None:
        result = prune_drafts(
            Path(args.draft_dir),
            older_than_days=args.prune_drafts_older_than_days,
        )
        payload["draft_prune"] = {"removed": result.removed, "kept": result.kept}

    if args.vacuum_db:
        payload["sqlite"] = maintain_sqlite(Path(args.state_db))

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
