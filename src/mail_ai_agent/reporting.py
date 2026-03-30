from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .schemas import WorkflowStatus
from .state_manager import MOVE_CLEANUP_PENDING_ACTION


def load_audit_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def summarize_audit_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts = Counter(record.get("action_taken") for record in records)
    status_counts = Counter(record.get("status_after") for record in records)
    category_counts = Counter(record.get("category") for record in records if record.get("category"))
    mailbox_counts = Counter(record.get("mailbox_id") for record in records if record.get("mailbox_id"))
    errors = [record for record in records if record.get("error")]
    cleanup_pending = sum(1 for record in records if record.get("action_taken") == MOVE_CLEANUP_PENDING_ACTION)
    return {
        "records": len(records),
        "actions": dict(sorted(action_counts.items())),
        "statuses": dict(sorted(status_counts.items())),
        "categories": dict(sorted(category_counts.items())),
        "mailboxes": dict(sorted(mailbox_counts.items())),
        "errors": len(errors),
        "cleanup_pending": cleanup_pending,
        "simulated": status_counts.get("simulated", 0),
        "cleanup_pass_processed": action_counts.get("cleanup_source", 0),
        "cleanup_uidvalidity_mismatch": action_counts.get("cleanup_uidvalidity_mismatch", 0),
    }


def export_audit_csv(records: list[dict[str, Any]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for record in records for key in record.keys()})
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(record)


def export_state_csv(db_path: Path, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM email_processing_state ORDER BY id")
        fieldnames: list[str] | None = None
        row_count = 0
        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = None
            for row in cursor:
                if fieldnames is None:
                    fieldnames = list(row.keys())
                    writer = csv.DictWriter(handle, fieldnames=fieldnames)
                    writer.writeheader()
                assert writer is not None
                writer.writerow(dict(row))
                row_count += 1
        if row_count == 0:
            destination.write_text("", encoding="utf-8")
    return row_count


def summarize_state(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"records": 0, "statuses": {}, "mailboxes": {}, "cleanup_pending": 0}
    with sqlite3.connect(db_path) as conn:
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
    }
