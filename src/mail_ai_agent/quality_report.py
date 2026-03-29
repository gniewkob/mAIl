from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .reporting import load_audit_records


def build_quality_payload(audit_path: Path) -> dict[str, Any]:
    records = load_audit_records(audit_path)
    mailbox_counts = Counter()
    category_counts = Counter()
    action_counts = Counter()
    route_source_counts = Counter()
    status_counts = Counter()
    target_folder_counts = Counter()
    recent_uncertain: list[dict[str, Any]] = []
    recent_failures: list[dict[str, Any]] = []

    for record in records:
        mailbox_id = record.get("mailbox_id")
        category = record.get("category")
        action = record.get("action_taken")
        status = record.get("status_after")
        target_folder = record.get("target_folder")

        if mailbox_id:
            mailbox_counts[str(mailbox_id)] += 1
        if category:
            category_counts[str(category)] += 1
        if action:
            action_counts[str(action)] += 1
        if status:
            status_counts[str(status)] += 1
        if target_folder:
            target_folder_counts[str(target_folder)] += 1

        route_source = _route_source(record)
        route_source_counts[route_source] += 1

        compact = {
            "timestamp": record.get("timestamp"),
            "mailbox_id": record.get("mailbox_id"),
            "category": record.get("category"),
            "action_taken": record.get("action_taken"),
            "target_folder": record.get("target_folder"),
            "subject": record.get("subject") or _hashed_field(record, "subject"),
            "sender": record.get("sender") or _hashed_field(record, "sender"),
            "error": record.get("error"),
        }
        if status == "uncertain":
            recent_uncertain.append(compact)
        if status in {"failed", "mailbox_failed", "cleanup_pending"}:
            recent_failures.append(compact)

    total = len(records)
    uncertain = status_counts.get("uncertain", 0)
    failed = status_counts.get("failed", 0) + status_counts.get("mailbox_failed", 0)
    cleanup_pending = status_counts.get("cleanup_pending", 0)
    llm_routed = route_source_counts.get("llm", 0)
    rule_routed = route_source_counts.get("rule", 0)

    return {
        "summary": {
            "records": total,
            "uncertain": uncertain,
            "failed": failed,
            "cleanup_pending": cleanup_pending,
            "llm_routed": llm_routed,
            "rule_routed": rule_routed,
            "llm_share": round(llm_routed / total, 4) if total else 0.0,
            "rule_share": round(rule_routed / total, 4) if total else 0.0,
        },
        "by_mailbox": dict(sorted(mailbox_counts.items())),
        "by_category": dict(sorted(category_counts.items())),
        "by_action": dict(sorted(action_counts.items())),
        "by_route_source": dict(sorted(route_source_counts.items())),
        "by_target_folder": dict(sorted(target_folder_counts.items())),
        "recent_uncertain": recent_uncertain[-20:],
        "recent_failures": recent_failures[-20:],
    }


def _hashed_field(record: dict[str, Any], field: str) -> str | None:
    value = record.get(f"{field}_sha256")
    if value:
        return f"sha256:{value}"
    return None


def _route_source(record: dict[str, Any]) -> str:
    action = str(record.get("action_taken") or "")
    if "route_from_llm" in action:
        return "llm"
    if "route_uncertain_llm_failure" in action:
        return "llm_failure"
    if "skip_ai" in action or "move_skip_ai" in action:
        return "rule"
    if action.startswith("move_route_uncertain") or action == "route_uncertain":
        return "uncertain"
    return "other"
