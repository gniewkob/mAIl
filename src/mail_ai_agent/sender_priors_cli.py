from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


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


def build_sender_priors(
    *,
    audit_log: Path,
    output_path: Path,
    window_days: int = 30,
    min_samples: int = 5,
    min_share: float = 0.92,
) -> dict[str, Any]:
    if not audit_log.exists():
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "by_mailbox": {}}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    by_mailbox_sender: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))

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
            if not action.endswith("route_from_llm"):
                continue
            mailbox_id = str(item.get("mailbox_id") or "").strip()
            sender_sha = str(item.get("sender_sha256") or "").strip()
            category = str(item.get("category") or "").strip().lower()
            if not mailbox_id or not sender_sha or not category:
                continue
            by_mailbox_sender[mailbox_id][sender_sha][category] += 1

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "min_samples": min_samples,
        "min_share": min_share,
        "by_mailbox": {},
    }

    for mailbox_id, senders in sorted(by_mailbox_sender.items()):
        sender_payload: dict[str, Any] = {}
        for sender_sha, cats in senders.items():
            total = sum(cats.values())
            if total < min_samples:
                continue
            category, top = sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            share = top / total if total else 0.0
            if share < min_share:
                continue
            sender_payload[sender_sha] = {
                "category": category,
                "samples": int(total),
                "share": round(share, 4),
            }
        payload["by_mailbox"][mailbox_id] = {"senders": sender_payload}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build per-sender category priors from audit log.")
    parser.add_argument("--audit-log", default="logs/multi-prod-audit.jsonl")
    parser.add_argument("--output", default="config/sender_priors.auto.json")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--min-share", type=float, default=0.92)
    args = parser.parse_args()

    payload = build_sender_priors(
        audit_log=Path(args.audit_log),
        output_path=Path(args.output),
        window_days=args.window_days,
        min_samples=args.min_samples,
        min_share=args.min_share,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
