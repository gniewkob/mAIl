"""Summarise classifier v2 shadow mode: where v2 would route differently from production.

  cd ~/Repos/priv/mAIl && .venv/bin/python ops/shadow_report.py [--days 7] [--list 40]
"inbox" = stays in INBOX (human category); the rows that matter are the INBOX flips:
  v1 moves it out, v2 keeps it   -> candidate rescued mail (check: was it yours?)
  v1 keeps it,     v2 moves it   -> candidate extra noise removed (check: was it noise?)
"""
from __future__ import annotations

import argparse
import collections
import json
from datetime import datetime, timedelta
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="logs/shadow-v2.jsonl")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--list", type=int, default=40)
    args = ap.parse_args()
    since = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%S")
    path = Path(args.log)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line] if path.exists() else []
    rows = [r for r in rows if r.get("ts", "") >= since]
    ok = [r for r in rows if "v2" in r]
    rescued = [r for r in ok if not r["inbox_v1"] and r["inbox_v2"]]
    removed = [r for r in ok if r["inbox_v1"] and not r["inbox_v2"]]
    lat = sorted(r["latency_ms"] for r in ok) or [0]
    print(f"shadow rows: {len(rows)} (errors {len(rows) - len(ok)}), since {since}")
    print(f"same INBOX decision: {len(ok) - len(rescued) - len(removed)}; "
          f"v2 keeps in INBOX (v1 moved out): {len(rescued)}; v2 moves out (v1 kept): {len(removed)}")
    print(f"v2 latency p50 {lat[len(lat) // 2]} ms; sources {dict(collections.Counter(r['source'] for r in ok))}")
    for title, items in (("v2 KEEPS in INBOX, production moved out", rescued),
                         ("v2 MOVES OUT, production kept in INBOX", removed)):
        print(f"\n## {title}")
        for r in items[-args.list:]:
            print(f"- {r['ts'][5:16]} {r['mailbox']:<20} {r['v1']:>11} -> {r['v2']:<11} [{r['source']}] "
                  f"{r['sender_domain']} | {r['subject'][:70]}")


if __name__ == "__main__":
    main()
