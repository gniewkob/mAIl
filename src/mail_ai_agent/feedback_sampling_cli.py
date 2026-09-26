from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Any

from .config import Settings
from .imap_client import IMAPClient


def _parse_ts(raw: object) -> datetime | None:
    if raw is None:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _state_uncertain_candidates(*, state_db: Path, limit: int) -> list[dict[str, Any]]:
    if not str(state_db) or not state_db.exists() or limit <= 0:
        return []
    with sqlite3.connect(state_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, mailbox_id, message_id, sender_sha256, subject_sha256, category, confidence, action_taken, updated_at
            FROM email_processing_state
            WHERE status = 'uncertain'
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        raw_message_id = str(row["message_id"] or "").strip()
        message_id_sha256 = (
            hashlib.sha256(raw_message_id.encode("utf-8")).hexdigest() if raw_message_id else f"state:{int(row['id'])}"
        )
        candidates.append(
            {
                "timestamp": row["updated_at"],
                "mailbox_id": row["mailbox_id"],
                "sender_sha256": row["sender_sha256"],
                "subject_sha256": row["subject_sha256"],
                "message_id_sha256": message_id_sha256,
                "predicted_category": row["category"] or "other",
                "action_taken": row["action_taken"] or "state_uncertain",
                "confidence": float(row["confidence"] or 0.0),
                "sender_events": 1,
                "review_reason": "state_uncertain_backlog",
                "state_record_id": int(row["id"]),
            }
        )
    return candidates


def _state_record_for_message_id(*, state_db: Path | None, mailbox_id: str, message_id: str | None) -> dict[str, Any] | None:
    if state_db is None or not state_db.exists() or not message_id:
        return None
    with sqlite3.connect(state_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, mailbox_id, message_id, sender_sha256, subject_sha256, category, confidence, action_taken, updated_at, status
            FROM email_processing_state
            WHERE mailbox_id = ? AND message_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (mailbox_id, message_id),
        ).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _category_from_keywords(keywords: set[str], tag_prefix: str) -> str | None:
    prefix = (tag_prefix or "$AI_").strip() or "$AI_"
    for keyword in sorted(keywords):
        raw = str(keyword).strip()
        if not raw.lower().startswith(prefix.lower()):
            continue
        suffix = raw[len(prefix):].strip().lower()
        if suffix:
            return suffix
    return None


def _imap_folder_candidates(
    *,
    settings: Settings | None,
    state_db: Path | None,
    mailbox_ids: set[str],
    folders: list[str],
    per_folder_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if settings is None or not folders or per_folder_limit <= 0:
        return [], []

    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for mailbox in settings.load_mailboxes():
        if mailbox_ids and mailbox.mailbox_id not in mailbox_ids:
            continue
        try:
            with IMAPClient(mailbox) as imap:
                for folder in folders:
                    try:
                        uids = imap.list_uids(folder)
                        if per_folder_limit > 0:
                            uids = uids[-per_folder_limit:]
                        if not uids:
                            continue
                        headers = imap.fetch_message_id_headers(folder, uids)
                        keywords_by_uid = imap.fetch_uid_keywords(folder, uids)
                    except Exception as exc:
                        errors.append(
                            {
                                "mailbox_id": mailbox.mailbox_id,
                                "folder": folder,
                                "error": str(exc),
                            }
                        )
                        continue
                    for uid in reversed(uids):
                        raw_message_id = str(headers.get(uid) or "").strip()
                        message_id_sha256 = (
                            hashlib.sha256(raw_message_id.encode("utf-8")).hexdigest() if raw_message_id else f"imap:{mailbox.mailbox_id}:{folder}:{uid}"
                        )
                        state_row = _state_record_for_message_id(
                            state_db=state_db,
                            mailbox_id=mailbox.mailbox_id,
                            message_id=raw_message_id or None,
                        )
                        keywords = keywords_by_uid.get(uid, set())
                        inferred_category = _category_from_keywords(keywords, settings.imap_tag_prefix)
                        predicted_category = (
                            str((state_row or {}).get("category") or "").strip().lower()
                            or inferred_category
                            or "other"
                        )
                        confidence_value = (state_row or {}).get("confidence")
                        try:
                            confidence = float(confidence_value or 0.0)
                        except Exception:
                            confidence = 0.0
                        review_reason = "imap_uncertain_folder" if folder == mailbox.imap_uncertain_folder else "imap_folder_backlog"
                        candidates.append(
                            {
                                "timestamp": (state_row or {}).get("updated_at"),
                                "mailbox_id": mailbox.mailbox_id,
                                "source_folder": folder,
                                "imap_uid": uid,
                                "sender_sha256": (state_row or {}).get("sender_sha256"),
                                "subject_sha256": (state_row or {}).get("subject_sha256"),
                                "message_id_sha256": message_id_sha256,
                                "predicted_category": predicted_category,
                                "action_taken": (state_row or {}).get("action_taken") or "imap_folder_review",
                                "confidence": confidence,
                                "sender_events": 1,
                                "review_reason": review_reason,
                                "state_record_id": (state_row or {}).get("id"),
                                "state_status": (state_row or {}).get("status"),
                                "imap_keywords": sorted(str(item) for item in keywords),
                            }
                        )
        except Exception as exc:
            errors.append(
                {
                    "mailbox_id": mailbox.mailbox_id,
                    "folder": "__mailbox__",
                    "error": str(exc),
                }
            )
    return candidates, errors


def build_feedback_candidates(
    *,
    audit_log: Path,
    env_file: Path | None,
    state_db: Path | None,
    output_path: Path,
    mailbox_ids: set[str] | None = None,
    imap_folders: list[str] | None = None,
    imap_per_folder_limit: int = 20,
    window_hours: int = 24,
    limit: int = 30,
    low_conf_threshold: float = 0.96,
) -> dict[str, Any]:
    if not audit_log.exists():
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_hours": window_hours,
            "limit": limit,
            "scanned": 0,
            "candidates": [],
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    mailbox_scope = mailbox_ids or set()
    settings = None
    if env_file is not None:
        settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    by_sender: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scanned = 0
    with audit_log.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            ts = _parse_ts(item.get("timestamp"))
            if ts is None or ts < cutoff:
                continue
            action = str(item.get("action_taken") or "").strip().lower()
            status = str(item.get("status_after") or "").strip().lower()
            if status != "processed":
                continue
            if not (action.startswith("tag_route_") or action.startswith("move_route_") or action == "tag_skip_ai"):
                continue
            sender = str(item.get("sender_sha256") or "").strip()
            if not sender:
                continue
            scanned += 1
            by_sender[sender].append(item)

    low_conf: list[dict[str, Any]] = []
    novel_sender: list[dict[str, Any]] = []

    for sender, rows in by_sender.items():
        rows_sorted = sorted(rows, key=lambda r: str(r.get("timestamp") or ""), reverse=True)
        first = rows_sorted[0]
        try:
            conf = float(first.get("confidence", 1.0) or 1.0)
        except Exception:
            conf = 1.0
        base = {
            "timestamp": first.get("timestamp"),
            "mailbox_id": first.get("mailbox_id"),
            "sender_sha256": sender,
            "subject_sha256": first.get("subject_sha256"),
            "message_id_sha256": first.get("message_id_sha256"),
            "predicted_category": first.get("category"),
            "action_taken": first.get("action_taken"),
            "confidence": conf,
            "sender_events": len(rows),
        }
        if conf <= low_conf_threshold:
            cand = dict(base)
            cand["review_reason"] = "low_confidence"
            low_conf.append(cand)
        if len(rows) == 1:
            cand = dict(base)
            cand["review_reason"] = "novel_sender"
            novel_sender.append(cand)

    low_conf.sort(key=lambda x: (float(x.get("confidence", 1.0)), str(x.get("timestamp") or "")))
    novel_sender.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
    uncertain_backlog = _state_uncertain_candidates(state_db=state_db or Path("/nonexistent"), limit=limit)
    imap_candidates, imap_errors = _imap_folder_candidates(
        settings=settings,
        state_db=state_db,
        mailbox_ids=mailbox_scope,
        folders=[item for item in (imap_folders or []) if str(item).strip()],
        per_folder_limit=max(0, int(imap_per_folder_limit)),
    )

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in imap_candidates + uncertain_backlog + low_conf + novel_sender:
        key = str(row.get("message_id_sha256") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(row)
        if len(merged) >= limit:
            break

    payload = {
        "generated_at": now.isoformat(),
        "window_hours": window_hours,
        "limit": limit,
        "low_conf_threshold": low_conf_threshold,
        "scanned": scanned,
        "state_uncertain_candidates": len(uncertain_backlog),
        "imap_candidates": len(imap_candidates),
        "imap_errors": imap_errors,
        "candidates": merged,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily active-learning feedback candidates.")
    parser.add_argument("--env-file", default=None, help="Optional env file path for IMAP-backed candidate sampling")
    parser.add_argument("--audit-log", default="logs/multi-prod-audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--state-db", default="data/multi-prod-state.sqlite", help="Optional SQLite state DB for current uncertain backlog.")
    parser.add_argument("--output", default="logs/feedback-candidates-daily.json", help="Output JSON path")
    parser.add_argument("--mailbox-id", action="append", default=[], help="Optional mailbox id filter (repeatable)")
    parser.add_argument("--imap-folder", action="append", default=[], help="Optional IMAP folder to sample directly (repeatable)")
    parser.add_argument("--imap-per-folder-limit", type=int, default=20, help="How many newest messages to inspect per IMAP folder.")
    parser.add_argument("--window-hours", type=int, default=24, help="Lookback window in hours")
    parser.add_argument("--limit", type=int, default=30, help="Maximum candidates")
    parser.add_argument("--low-conf-threshold", type=float, default=0.96, help="Confidence threshold for low-confidence queue")
    args = parser.parse_args()

    payload = build_feedback_candidates(
        audit_log=Path(args.audit_log),
        env_file=Path(args.env_file) if args.env_file else None,
        state_db=Path(args.state_db) if args.state_db else None,
        output_path=Path(args.output),
        mailbox_ids={str(item).strip() for item in args.mailbox_id if str(item).strip()},
        imap_folders=[str(item).strip() for item in args.imap_folder if str(item).strip()],
        imap_per_folder_limit=max(0, int(args.imap_per_folder_limit)),
        window_hours=args.window_hours,
        limit=args.limit,
        low_conf_threshold=args.low_conf_threshold,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
