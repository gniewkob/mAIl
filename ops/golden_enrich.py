"""Add thread headers (In-Reply-To, References, Auto-Submitted, To) to the golden sample (read-only IMAP).

golden_export kept only a few headers; the v2 pipeline needs thread headers to detect replies to
mail the owner sent. Messages are found by recomputing the sample id over every folder.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/golden_enrich.py
"""
from __future__ import annotations

import email
import hashlib
import json
import os
from datetime import datetime, timedelta
from email.policy import default as default_policy
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.imap_client import IMAPClient
from mail_ai_agent.sent_index import _quote

SAMPLE = Path("data/golden/golden_sample.jsonl")
FIELDS = ("in-reply-to", "references", "auto-submitted", "to")


def _sid(box: str, mid: str) -> str:
    return hashlib.sha256(f"{box}|{mid}".encode()).hexdigest()[:12]


def main() -> None:
    settings = Settings(_env_file=".env.multi.prod")  # type: ignore[call-arg]
    rows = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line]
    wanted = {r["id"]: r for r in rows}
    boxes = {m.mailbox_id: m for m in settings.load_mailboxes()}
    since = (datetime.now() - timedelta(days=420)).strftime("%d-%b-%Y")
    found = 0
    for box in sorted({r["mailbox"] for r in rows}):
        with IMAPClient(boxes[box]) as client:
            conn = client.connection
            for folder in client.list_folders(force_refresh=True):
                if conn.select(_quote(folder), readonly=True)[0] != "OK":
                    continue
                st, d = conn.uid("search", None, "SINCE", since)
                uids = d[0].split() if st == "OK" and d and d[0] else []
                for i in range(0, len(uids), 300):
                    st, parts = conn.uid("fetch", b",".join(uids[i:i + 300]).decode(),
                                         "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES AUTO-SUBMITTED TO)])")
                    for part in parts or []:
                        if not isinstance(part, tuple):
                            continue
                        msg = email.message_from_bytes(part[1], policy=default_policy)
                        raw = email.message_from_bytes(part[1])
                        for mid in {str(raw.get("Message-ID") or ""), str(raw.get("Message-ID") or "").strip(),
                                    str(msg.get("Message-ID") or "").strip()}:
                            sid = _sid(box, mid) if mid else None
                            if sid in wanted and "in-reply-to" not in wanted[sid].get("enriched", {}):
                                wanted[sid]["enriched"] = {f: str(raw.get(f) or "").strip() for f in FIELDS}
                                found += 1
    for r in rows:
        r.setdefault("headers", {}).update({k: v for k, v in r.pop("enriched", {}).items() if v})
    fd = os.open(SAMPLE, os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(json.dumps({"samples": len(rows), "found": found,
                      "with_in_reply_to": sum(1 for r in rows if r["headers"].get("in-reply-to"))}))


if __name__ == "__main__":
    main()
