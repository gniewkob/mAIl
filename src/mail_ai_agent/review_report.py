from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .reporting import load_audit_records


def build_review_rows(audit_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in load_audit_records(audit_path):
        rows.append(
            {
                "timestamp": record.get("timestamp"),
                "mailbox_id": record.get("mailbox_id"),
                "mailbox_user": record.get("mailbox_user"),
                "message_id": record.get("message_id"),
                "sender": record.get("sender"),
                "subject": record.get("subject"),
                "status_after": record.get("status_after"),
                "category": record.get("category"),
                "confidence": record.get("confidence"),
                "target_folder": record.get("target_folder"),
                "action_taken": record.get("action_taken"),
                "draft_path": record.get("draft_path"),
                "error": record.get("error"),
            }
        )
    return rows


def export_review_csv(rows: list[dict[str, Any]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "timestamp",
        "mailbox_id",
        "mailbox_user",
        "message_id",
        "sender",
        "subject",
        "status_after",
        "category",
        "confidence",
        "target_folder",
        "action_taken",
        "draft_path",
        "error",
    ]
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize_review_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"rows": len(rows), "uncertain": 0, "failed": 0, "drafts": 0}
    for row in rows:
        if row["status_after"] == "uncertain":
            summary["uncertain"] += 1
        if row["status_after"] == "failed":
            summary["failed"] += 1
        if row["draft_path"]:
            summary["drafts"] += 1
    return summary


def review_report_json(audit_path: Path) -> str:
    rows = build_review_rows(audit_path)
    payload = {"summary": summarize_review_rows(rows), "rows": rows}
    return json.dumps(payload, ensure_ascii=False, indent=2)
