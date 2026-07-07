from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from .schemas import WorkflowStatus
from .state_manager import MOVE_CLEANUP_PENDING_ACTION

PII_FIELDS = {"sender", "subject", "message_id", "draft_path"}


def _mask_pii(record: dict[str, Any]) -> dict[str, Any]:
    """Replace PII field values with [redacted]."""
    masked = dict(record)
    for field in PII_FIELDS:
        if field in masked and masked[field] not in (None, "", "[redacted]"):
            masked[field] = "[redacted]"
    return masked


def tail_audit_records(path: Path, n: int) -> list[dict[str, Any]]:
    """Read the last n JSONL records from an audit log without loading the full file."""
    if not path.exists():
        return []
    if n <= 0:
        return []

    chunk_size = 8192
    collected_lines: list[bytes] = []
    total_complete = 0

    with path.open("rb") as handle:
        handle.seek(0, 2)  # seek to end
        file_size = handle.tell()
        position = file_size
        remainder = b""

        while position > 0 and total_complete < n:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size) + remainder
            lines = chunk.split(b"\n")
            remainder = lines[0]
            for line in reversed(lines[1:]):
                stripped = line.strip()
                if stripped:
                    collected_lines.append(stripped)
                    total_complete += 1
                    if total_complete >= n:
                        break

        # Don't forget the remainder if we reached start of file
        if position == 0 and total_complete < n and remainder.strip():
            collected_lines.append(remainder.strip())

    records: list[dict[str, Any]] = []
    for line in reversed(collected_lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def load_audit_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def summarize_audit_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts = Counter(record.get("action_taken") for record in records)
    status_counts = Counter(record.get("status_after") for record in records)
    category_counts = Counter(record.get("category") for record in records if record.get("category"))
    mailbox_counts = Counter(record.get("mailbox_id") for record in records if record.get("mailbox_id"))
    errors = [record for record in records if record.get("error")]
    expected_skip_actions = {"skip_already_done", "skip_conflict", "skip_locked"}
    processing_failure_actions = {
        "failed",
        "failed_parse",
        "failed_classify",
        "failed_route",
        "mailbox_failed",
        "imap_auth_failed",
        "move_copy_succeeded_cleanup_pending",
        "move_route_uncertain_llm_failure",
        "move_route_uncertain_parse_failure",
        "cleanup_source_already_done_failed",
    }
    expected_skips = sum(int(action_counts.get(action, 0)) for action in expected_skip_actions)
    processing_failures = sum(int(action_counts.get(action, 0)) for action in processing_failure_actions)
    errors_excluding_expected_skips = max(0, len(errors) - expected_skips)
    cleanup_pending = status_counts.get(WorkflowStatus.CLEANUP_PENDING.value, 0)
    return {
        "records": len(records),
        "actions": dict(sorted(action_counts.items())),
        "statuses": dict(sorted(status_counts.items())),
        "categories": dict(sorted(category_counts.items())),
        "mailboxes": dict(sorted(mailbox_counts.items())),
        "errors": len(errors),
        "errors_excluding_expected_skips": errors_excluding_expected_skips,
        "expected_skips": expected_skips,
        "processing_failures": processing_failures,
        "cleanup_pending": cleanup_pending,
        "simulated": status_counts.get("simulated", 0),
        "cleanup_pass_processed": action_counts.get("cleanup_source", 0),
        "cleanup_uidvalidity_mismatch": action_counts.get("cleanup_uidvalidity_mismatch", 0),
    }


def export_audit_csv(records: list[dict[str, Any]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for record in records for key in record.keys()})
    tmp = destination.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(_mask_pii(record))
    tmp.replace(destination)


def export_state_csv(db_path: Path, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(".tmp")
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM email_processing_state ORDER BY id")
        fieldnames: list[str] | None = None
        row_count = 0
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            writer = None
            for row in cursor:
                if fieldnames is None:
                    fieldnames = list(row.keys())
                    writer = csv.DictWriter(handle, fieldnames=fieldnames)
                    writer.writeheader()
                if writer is None:
                    raise RuntimeError("CSV writer not initialized")
                writer.writerow(_mask_pii(dict(row)))
                row_count += 1
    tmp.replace(destination)
    return row_count


def summarize_state(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "records": 0,
            "statuses": {},
            "mailboxes": {},
            "cleanup_pending": 0,
            "uncertain_by_mailbox": {},
            "failed_by_mailbox": {},
        }
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM email_processing_state
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
        mailbox_rows = conn.execute(
            """
            SELECT mailbox_id, COUNT(*) AS count
            FROM email_processing_state
            GROUP BY mailbox_id
            ORDER BY mailbox_id
            """
        ).fetchall()
        uncertain_mailbox_rows = conn.execute(
            """
            SELECT mailbox_id, COUNT(*) AS count
            FROM email_processing_state
            WHERE status = ?
            GROUP BY mailbox_id
            ORDER BY mailbox_id
            """,
            (WorkflowStatus.UNCERTAIN.value,),
        ).fetchall()
        failed_mailbox_rows = conn.execute(
            """
            SELECT mailbox_id, COUNT(*) AS count
            FROM email_processing_state
            WHERE status = ?
            GROUP BY mailbox_id
            ORDER BY mailbox_id
            """,
            (WorkflowStatus.FAILED.value,),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM email_processing_state").fetchone()[0]
        cleanup_pending = conn.execute(
            "SELECT COUNT(*) FROM email_processing_state WHERE status = ?",
            (WorkflowStatus.CLEANUP_PENDING.value,),
        ).fetchone()[0]
    return {
        "records": total,
        "statuses": {row["status"]: row["count"] for row in rows},
        "mailboxes": {row["mailbox_id"]: row["count"] for row in mailbox_rows},
        "cleanup_pending": cleanup_pending,
        "uncertain_by_mailbox": {row["mailbox_id"]: row["count"] for row in uncertain_mailbox_rows},
        "failed_by_mailbox": {row["mailbox_id"]: row["count"] for row in failed_mailbox_rows},
    }
