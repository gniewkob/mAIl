from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
from typing import Any

_RECENT_LIMIT = 20
_QUALITY_CACHE_LOCK = threading.Lock()
_ALLOWED_OBSERVABILITY_CATEGORIES = {
    "appointment",
    "question",
    "complaint",
    "billing",
    "system",
    "spam",
    "newsletter",
    "offer",
    "other",
    "parse_error",
}


@dataclass
class _QualityAccumulator:
    inode: int
    offset: int = 0
    mailbox_counts: Counter[str] = field(default_factory=Counter)
    category_counts: Counter[str] = field(default_factory=Counter)
    action_counts: Counter[str] = field(default_factory=Counter)
    route_source_counts: Counter[str] = field(default_factory=Counter)
    status_counts: Counter[str] = field(default_factory=Counter)
    target_folder_counts: Counter[str] = field(default_factory=Counter)
    recent_uncertain: list[dict[str, Any]] = field(default_factory=list)
    recent_failures: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0

    def apply_record(self, record: dict[str, Any]) -> None:
        record = _normalize_observability_record(record)
        mailbox_id = record.get("mailbox_id")
        category = record.get("category")
        action = record.get("action_taken")
        status = record.get("status_after")
        target_folder = record.get("target_folder")

        self.total += 1
        if mailbox_id:
            self.mailbox_counts[str(mailbox_id)] += 1
        if category in _ALLOWED_OBSERVABILITY_CATEGORIES:
            self.category_counts[str(category)] += 1
        if action:
            self.action_counts[str(action)] += 1
        if status:
            self.status_counts[str(status)] += 1
        if target_folder:
            self.target_folder_counts[str(target_folder)] += 1

        route_source = _route_source(record)
        if route_source is not None:
            self.route_source_counts[route_source] += 1

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
            self.recent_uncertain.append(compact)
            self.recent_uncertain = self.recent_uncertain[-_RECENT_LIMIT:]
        if status in {"failed", "mailbox_failed", "imap_auth_failed", "cleanup_pending"}:
            self.recent_failures.append(compact)
            self.recent_failures = self.recent_failures[-_RECENT_LIMIT:]

    def to_payload(self) -> dict[str, Any]:
        uncertain = self.status_counts.get("uncertain", 0)
        failed = (
            self.status_counts.get("failed", 0)
            + self.status_counts.get("mailbox_failed", 0)
            + self.status_counts.get("imap_auth_failed", 0)
        )
        cleanup_pending = self.status_counts.get("cleanup_pending", 0)
        llm_routed = self.route_source_counts.get("llm", 0)
        rule_routed = self.route_source_counts.get("rule", 0)

        routed_total = sum(self.route_source_counts.values())

        return {
            "summary": {
                "records": self.total,
                "uncertain": uncertain,
                "failed": failed,
                "cleanup_pending": cleanup_pending,
                "llm_routed": llm_routed,
                "rule_routed": rule_routed,
                "routed_records": routed_total,
                "llm_share": round(llm_routed / routed_total, 4) if routed_total else 0.0,
                "rule_share": round(rule_routed / routed_total, 4) if routed_total else 0.0,
            },
            "by_mailbox": dict(sorted(self.mailbox_counts.items())),
            "by_category": dict(sorted(self.category_counts.items())),
            "by_action": dict(sorted(self.action_counts.items())),
            "by_route_source": dict(sorted(self.route_source_counts.items())),
            "by_target_folder": dict(sorted(self.target_folder_counts.items())),
            "recent_uncertain": list(self.recent_uncertain),
            "recent_failures": list(self.recent_failures),
        }


_QUALITY_CACHE: dict[str, _QualityAccumulator] = {}


def build_quality_payload(audit_path: Path, *, window_days: int | None = None) -> dict[str, Any]:
    if window_days is not None and window_days > 0:
        return _build_windowed_quality_payload(audit_path, window_days=window_days)
    return _get_cached_quality_payload(audit_path)


def _get_cached_quality_payload(audit_path: Path) -> dict[str, Any]:
    key = str(audit_path.resolve())
    if not audit_path.exists():
        with _QUALITY_CACHE_LOCK:
            _QUALITY_CACHE.pop(key, None)
        return _QualityAccumulator(inode=0).to_payload()

    stat = audit_path.stat()
    inode = int(stat.st_ino)
    size = int(stat.st_size)

    with _QUALITY_CACHE_LOCK:
        cached = _QUALITY_CACHE.get(key)
        if cached is None or cached.inode != inode or size < cached.offset:
            cached = _QualityAccumulator(inode=inode)
            _QUALITY_CACHE[key] = cached
            _read_records_from_offset(audit_path, cached, 0)
            return cached.to_payload()

        if size > cached.offset:
            _read_records_from_offset(audit_path, cached, cached.offset)
        return cached.to_payload()


def _build_windowed_quality_payload(audit_path: Path, *, window_days: int) -> dict[str, Any]:
    if not audit_path.exists():
        return _QualityAccumulator(inode=0).to_payload()

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    accumulator = _QualityAccumulator(inode=0)
    with audit_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            if _record_is_newer_than_cutoff(record, cutoff=cutoff):
                accumulator.apply_record(record)
    return accumulator.to_payload()


def _read_records_from_offset(audit_path: Path, accumulator: _QualityAccumulator, offset: int) -> None:
    with audit_path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if isinstance(record, dict):
                accumulator.apply_record(record)
        accumulator.offset = handle.tell()


def _record_is_newer_than_cutoff(record: dict[str, Any], *, cutoff: datetime) -> bool:
    raw = record.get("timestamp")
    if raw is None:
        return False
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


def _hashed_field(record: dict[str, Any], field: str) -> str | None:
    value = record.get(f"{field}_sha256")
    if value:
        return f"sha256:{value}"
    return None


def _normalize_observability_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    category = str(normalized.get("category") or "").strip().lower()
    action = str(normalized.get("action_taken") or "").strip().lower()
    target_folder = str(normalized.get("target_folder") or "").strip()
    error = str(normalized.get("error") or "").strip().lower()

    if action == "move_route_uncertain_parse_failure":
        normalized["category"] = "parse_error"
    elif action == "move_copy_succeeded_cleanup_pending" and error.startswith("parse_failed:"):
        normalized["category"] = "parse_error"

    return normalized


def _route_source(record: dict[str, Any]) -> str | None:
    action = str(record.get("action_taken") or "")
    if "route_from_llm" in action:
        return "llm"
    if "route_uncertain_llm_failure" in action:
        return "llm_failure"
    if "skip_ai" in action or "move_skip_ai" in action:
        return "rule"
    if action.startswith("move_route_uncertain") or action == "route_uncertain":
        return "uncertain"
    return None
