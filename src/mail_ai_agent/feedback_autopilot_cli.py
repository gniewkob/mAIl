from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .quality_feedback_cli import append_feedback


def _parse_ts(raw: object) -> datetime | None:
    if raw is None:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def run_autopilot(
    *,
    audit_log: Path,
    feedback_log: Path,
    window_hours: int = 24,
    max_appends: int = 50,
) -> dict[str, Any]:
    if not audit_log.exists():
        return {"ok": True, "scanned": 0, "appended": 0, "reason": "missing_audit"}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)

    seen_feedback_keys: set[str] = set()
    if feedback_log.exists():
        for line in feedback_log.read_text(encoding="utf-8").splitlines()[-5000:]:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            key = str(item.get("message_id_sha256") or "").strip()
            mailbox_id = str(item.get("mailbox_id") or "").strip()
            if key and mailbox_id:
                seen_feedback_keys.add(f"{mailbox_id}:{key}")

    by_message: dict[str, list[dict[str, Any]]] = {}
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
            key = str(item.get("message_id_sha256") or "").strip()
            mailbox_id = str(item.get("mailbox_id") or "").strip()
            if not key or not mailbox_id:
                continue
            action = str(item.get("action_taken") or "").strip().lower()
            status = str(item.get("status_after") or "").strip().lower()
            if status != "processed":
                continue
            if not (action.startswith("tag_route_") or action.startswith("move_route_")):
                continue
            bucket_key = f"{mailbox_id}:{key}"
            by_message.setdefault(bucket_key, []).append(item)

    appended = 0
    scanned = len(by_message)
    for scoped_key, rows in by_message.items():
        if appended >= max_appends:
            break
        if scoped_key in seen_feedback_keys:
            continue
        rows_sorted = sorted(rows, key=lambda r: str(r.get("timestamp") or ""))
        if len(rows_sorted) < 2:
            continue
        categories = [str(r.get("category") or "").strip().lower() for r in rows_sorted]
        non_empty_categories = [c for c in categories if c]
        unique_categories = set(non_empty_categories)
        # Ignore noisy oscillations; append only stable two-state corrections.
        if len(unique_categories) > 2:
            continue
        if len(non_empty_categories) >= 3 and non_empty_categories[-1] != non_empty_categories[-2]:
            continue
        first = rows_sorted[0]
        last = rows_sorted[-1]
        # Safety: ensure same message identity and bounded time gap.
        first_mid = str(first.get("message_id_sha256") or "").strip()
        last_mid = str(last.get("message_id_sha256") or "").strip()
        first_mbox = str(first.get("mailbox_id") or "").strip()
        last_mbox = str(last.get("mailbox_id") or "").strip()
        if not first_mid or first_mid != last_mid or not first_mbox or first_mbox != last_mbox:
            continue
        first_fp = str(first.get("fingerprint") or "").strip()
        last_fp = str(last.get("fingerprint") or "").strip()
        if first_fp and last_fp and first_fp != last_fp:
            continue
        first_ts = _parse_ts(first.get("timestamp"))
        last_ts = _parse_ts(last.get("timestamp"))
        if first_ts is None or last_ts is None:
            continue
        if (last_ts - first_ts) > timedelta(hours=window_hours):
            continue
        pred = str(first.get("category") or "").strip().lower()
        corr = str(last.get("category") or "").strip().lower()
        if not pred or not corr or pred == corr:
            continue

        append_feedback(
            feedback_log=feedback_log,
            mailbox_id=last_mbox,
            message_id=f"sha256:{last_mid}",
            predicted_category=pred,
            corrected_category=corr,
            reason="autopilot_conflict_reclassification",
            message_id_sha256=last_mid,
            prediction_timestamp=str(first.get("timestamp") or ""),
            outcome_ok=False,
            outcome_note="category changed on repeated processing",
        )
        seen_feedback_keys.add(scoped_key)
        appended += 1

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "window_hours": window_hours,
        "scanned": scanned,
        "appended": appended,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-append feedback from explicit category conflicts in audit log.")
    parser.add_argument("--audit-log", default="logs/multi-prod-audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--feedback-log", default="logs/quality-feedback.jsonl", help="Path to feedback JSONL")
    parser.add_argument("--window-hours", type=int, default=24, help="Lookback window in hours")
    parser.add_argument("--max-appends", type=int, default=50, help="Max feedback records to append")
    args = parser.parse_args()

    payload = run_autopilot(
        audit_log=Path(args.audit_log),
        feedback_log=Path(args.feedback_log),
        window_hours=args.window_hours,
        max_appends=args.max_appends,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
