#!/usr/bin/env bash
# Weekly consistency check (read-only): count redundant copies of the same message across folders.
# Writes logs/consistency-latest.json, exported by metrics_exporter as mailai_consistency_* and alerted
# on in Prometheus (MailAIDuplicatesDetected). Nothing in the mailboxes is changed.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OUT="logs/consistency-latest.json"
PYTHONPATH=src .venv/bin/python ops/consolidate_folders.py --env-file .env.multi.prod 2>&1 \
| .venv/bin/python -c '
import json, sys, time
rows = [json.loads(l) for l in sys.stdin if l.startswith("{")]
payload = {
    "generated_at": int(time.time()),
    "redundant_copies": sum(r.get("stats", {}).get("copies_removed", 0) for r in rows),
    "booksy_to_archive": sum(r.get("stats", {}).get("booksy_archive", 0) for r in rows),
    "mailbox_errors": sum(1 for r in rows if r.get("error")),
    "by_mailbox": {r["mailbox_id"]: r.get("stats", {}).get("copies_removed", 0) for r in rows},
}
print(json.dumps(payload))
' > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
cat "$OUT"
