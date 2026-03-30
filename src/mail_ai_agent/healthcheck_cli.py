from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .reporting import load_audit_records, summarize_state, tail_audit_records


def _recent_records(path: Path, limit: int, *, max_age_minutes: int | None) -> list[dict]:
    if max_age_minutes is None and limit > 0:
        # Fast path: tail-read only the last N records without loading the full file
        return tail_audit_records(path, limit)
    records = load_audit_records(path)
    if max_age_minutes is not None and max_age_minutes >= 0:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        records = [record for record in records if _record_timestamp(record) and _record_timestamp(record) >= cutoff]
    if limit <= 0:
        return records
    return records[-limit:]


def _record_timestamp(record: dict) -> datetime | None:
    raw = record.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def build_health_payload(
    *,
    state_db: Path,
    audit_log: Path,
    stdout_log: Path | None,
    stderr_log: Path | None,
    recent_audit_limit: int,
    recent_audit_max_age_minutes: int | None,
    max_uncertain: int,
) -> dict[str, object]:
    state = summarize_state(state_db)
    recent = _recent_records(audit_log, recent_audit_limit, max_age_minutes=recent_audit_max_age_minutes)
    issues: list[str] = []

    statuses = state.get("statuses", {})
    failed = int(statuses.get("failed", 0))
    cleanup_pending = int(state.get("cleanup_pending", 0))
    uncertain = int(statuses.get("uncertain", 0))

    if failed > 0:
        issues.append(f"state_failed={failed}")
    if cleanup_pending > 0:
        issues.append(f"state_cleanup_pending={cleanup_pending}")
    if uncertain > max_uncertain:
        issues.append(f"state_uncertain={uncertain} exceeds max_uncertain={max_uncertain}")

    recent_actions = [str(record.get("action_taken") or "") for record in recent]
    recent_statuses = [str(record.get("status_after") or "") for record in recent]
    if any(status == "mailbox_failed" for status in recent_statuses):
        issues.append("recent mailbox_failed present in audit log")
    if any(action == "cleanup_uidvalidity_mismatch" for action in recent_actions):
        issues.append("recent cleanup_uidvalidity_mismatch present in audit log")
    if any("Refusing folder-level expunge" in str(record.get("error") or "") for record in recent):
        issues.append("recent folder-level expunge refusal present in audit log")

    stderr_size = stderr_log.stat().st_size if stderr_log and stderr_log.exists() else 0
    stdout_size = stdout_log.stat().st_size if stdout_log and stdout_log.exists() else 0

    payload = {
        "ok": not issues,
        "issues": issues,
        "state": {
            "records": int(state.get("records", 0)),
            "processed": int(statuses.get("processed", 0)),
            "uncertain": uncertain,
            "failed": failed,
            "cleanup_pending": cleanup_pending,
        },
        "recent_audit_records_checked": len(recent),
        "recent_audit_max_age_minutes": recent_audit_max_age_minutes,
        "stdout_log_size": stdout_size,
        "stderr_log_size": stderr_size,
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Operational healthcheck for AI Mail Triage")
    parser.add_argument("--audit-log", default="logs/audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--state-db", default="data/state.sqlite", help="Path to SQLite state DB")
    parser.add_argument("--stdout-log", default=None, help="Optional stdout log path")
    parser.add_argument("--stderr-log", default=None, help="Optional stderr log path")
    parser.add_argument("--recent-audit-limit", type=int, default=50, help="How many recent audit records to inspect")
    parser.add_argument(
        "--recent-audit-max-age-minutes",
        type=int,
        default=15,
        help="Only inspect audit records newer than this many minutes",
    )
    parser.add_argument("--max-uncertain", type=int, default=0, help="Maximum tolerated uncertain rows in state")
    args = parser.parse_args()

    payload = build_health_payload(
        state_db=Path(args.state_db),
        audit_log=Path(args.audit_log),
        stdout_log=Path(args.stdout_log) if args.stdout_log else None,
        stderr_log=Path(args.stderr_log) if args.stderr_log else None,
        recent_audit_limit=args.recent_audit_limit,
        recent_audit_max_age_minutes=args.recent_audit_max_age_minutes,
        max_uncertain=args.max_uncertain,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
