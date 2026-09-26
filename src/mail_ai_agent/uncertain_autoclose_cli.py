from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .audit_logger import AuditLogger
from .config import Settings
from .constants import ActionTaken, WorkflowStatus
from .reporting import is_exploration_uncertain_action
from .state_manager import StateManager


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def run_autoclose(
    *,
    state_db: Path,
    audit_log: Path,
    older_than_hours: int,
    limit: int,
    dry_run: bool,
    mailbox_id: str | None,
    redact_pii: bool,
    fsync_audit: bool,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(0, int(older_than_hours)))

    state = StateManager(state_db)
    audit = AuditLogger(audit_log, redact_pii=redact_pii, fsync=fsync_audit)

    records = state.list_by_status(status=WorkflowStatus.UNCERTAIN, mailbox_id=mailbox_id)
    candidates = []
    for record in records:
        if not is_exploration_uncertain_action(record.action_taken):
            continue
        updated_at = _parse_dt(record.updated_at)
        if updated_at is None or updated_at > cutoff:
            continue
        candidates.append((updated_at, record))

    candidates.sort(key=lambda item: item[0])
    if limit > 0:
        candidates = candidates[:limit]

    closed = 0
    failed = 0
    results: list[dict[str, Any]] = []
    for updated_at, record in candidates:
        item: dict[str, Any] = {
            "record_id": record.id,
            "mailbox_id": record.mailbox_id,
            "imap_uid": record.imap_uid,
            "status_before": record.status.value,
            "updated_at": updated_at.isoformat(),
            "action_taken_before": record.action_taken,
            "eligible_cutoff": cutoff.isoformat(),
            "status": "pending",
            "error": None,
        }
        results.append(item)

        if dry_run:
            item["status"] = "would_close"
            continue

        try:
            category = (record.category or "other").strip().lower() or "other"
            target_folder = record.target_folder or "INBOX.AI-Uncertain"
            state.mark_processed(
                record.id,
                category=category,
                confidence=record.confidence,
                target_folder=target_folder,
                target_uid=record.target_uid,
                action_taken=ActionTaken.AUTO_CLOSE_UNCERTAIN_EXPLORATION.value,
            )
            audit.log(
                level="INFO",
                mailbox_id=record.mailbox_id,
                source_folder=record.source_folder,
                message_id=record.message_id,
                fingerprint=record.fingerprint,
                imap_uid=record.imap_uid,
                sender=record.sender,
                subject=record.subject,
                status_before=WorkflowStatus.UNCERTAIN.value,
                status_after=WorkflowStatus.PROCESSED.value,
                category=category,
                confidence=record.confidence,
                action_taken=ActionTaken.AUTO_CLOSE_UNCERTAIN_EXPLORATION.value,
                target_folder=target_folder,
                target_uid=record.target_uid,
                error=f"auto_close_exploration_ttl_hours={int(older_than_hours)}",
                dry_run=False,
            )
            item["status"] = "closed"
            closed += 1
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            failed += 1

    return {
        "timestamp": now.isoformat(),
        "state_db": str(state_db),
        "audit_log": str(audit_log),
        "mailbox_id": mailbox_id,
        "older_than_hours": int(older_than_hours),
        "limit": int(limit),
        "dry_run": bool(dry_run),
        "selected": len(candidates),
        "closed": closed,
        "failed": failed,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-close stale exploration uncertain records.")
    parser.add_argument("--env-file", default=None, help="Optional env file path")
    parser.add_argument("--state-db", default=None, help="Override state DB path")
    parser.add_argument("--audit-log", default=None, help="Override audit JSONL path")
    parser.add_argument("--mailbox-id", default=None, help="Optional mailbox id filter")
    parser.add_argument("--older-than-hours", type=int, default=24, help="Close only records older than this many hours")
    parser.add_argument("--limit", type=int, default=500, help="Maximum records to close per run (0 = no limit)")
    parser.add_argument("--dry-run", action="store_true", help="Only report candidates without applying changes")
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()  # type: ignore[call-arg]
    state_db = Path(args.state_db) if args.state_db else settings.state_db_path
    audit_log = Path(args.audit_log) if args.audit_log else settings.audit_log_path

    payload = run_autoclose(
        state_db=state_db,
        audit_log=audit_log,
        older_than_hours=max(0, int(args.older_than_hours)),
        limit=max(0, int(args.limit)),
        dry_run=bool(args.dry_run),
        mailbox_id=args.mailbox_id or None,
        redact_pii=settings.audit_redact_pii,
        fsync_audit=settings.audit_fsync,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
