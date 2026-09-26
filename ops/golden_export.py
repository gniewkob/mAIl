"""Export a stratified sample of real mail for human labelling (read-only IMAP).

Writes data/golden/golden_sample.jsonl (full text for model benchmarks) and
data/golden/golden_labels.csv (what you label). Both 0600, under data/ (git-ignored).
Only the owner's and the business mailboxes are sampled.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/golden_export.py [--per-category 25]
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import os
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.email_parser import parse_email
from mail_ai_agent.imap_client import IMAPClient

MAILBOXES = ["gniewko_bodora", "kontakt_salon_bw", "kontakt_bodora", "kontakt_gliwicka111"]
CATS = ["appointment", "question", "complaint", "other", "billing", "system", "newsletter", "offer", "spam"]
OUT = Path("data/golden")


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--per-category", type=int, default=25)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--seed", type=int, default=20260926)
    args = ap.parse_args()
    settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    prefix = settings.imap_tag_prefix
    rng = random.Random(args.seed)
    since = (datetime.now() - timedelta(days=args.days)).strftime("%d-%b-%Y")
    pool: dict[str, list[tuple[str, str, str]]] = collections.defaultdict(list)  # cat -> (mailbox, folder, uid)
    boxes = {m.mailbox_id: m for m in settings.load_mailboxes() if m.mailbox_id in MAILBOXES}
    for box_id, mb in boxes.items():
        folder_cat = {mb.imap_newsletter_folder: "newsletter", mb.imap_offer_folder: "offer",
                      mb.imap_billing_folder: "billing", mb.imap_system_folder: "system",
                      mb.imap_spam_folder: "spam", mb.imap_source_folder: None}
        with IMAPClient(mb) as client:
            conn = client.connection
            for folder, cat in folder_cat.items():
                if conn.select(folder, readonly=True)[0] != "OK":
                    continue
                st, d = conn.uid("search", None, "SINCE", since)
                uids = d[0].split() if st == "OK" and d and d[0] else []
                for i in range(0, len(uids), 500):
                    st, rows = conn.uid("fetch", b",".join(uids[i:i + 500]).decode(), "(UID FLAGS)")
                    for r in rows or []:
                        line = r.decode() if isinstance(r, bytes) else r[0].decode()
                        uid = re.search(r"UID (\d+)", line)
                        if not uid:
                            continue
                        c = cat
                        if c is None:
                            tags = [t[len(prefix):] for t in re.findall(re.escape(prefix) + r"\w+", line) if "midless" not in t]
                            c = tags[0] if len(set(tags)) == 1 else None
                        if c in CATS:
                            pool[c].append((box_id, folder, uid.group(1)))
    picked = []
    for cat in CATS:
        items = pool.get(cat, [])
        rng.shuffle(items)
        picked += [(cat, *x) for x in items[: args.per_category]]
    rng.shuffle(picked)
    OUT.mkdir(parents=True, exist_ok=True)
    os.chmod(OUT, 0o700)
    samples, rows = [], []
    by_box = collections.defaultdict(list)
    for cat, box, folder, uid in picked:
        by_box[box].append((cat, folder, uid))
    for box, items in by_box.items():
        with IMAPClient(boxes[box]) as client:
            conn = client.connection
            for cat, folder, uid in items:
                conn.select(folder, readonly=True)
                st, data = conn.uid("fetch", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in data or [] if isinstance(p, tuple)), None)
                if not raw:
                    continue
                p = parse_email(raw, settings)
                sid = hashlib.sha256(f"{box}|{p.message_id or uid}".encode()).hexdigest()[:12]
                samples.append({"id": sid, "mailbox": box, "mail_category": cat, "sender": p.sender,
                                "subject": p.subject, "date": p.date.isoformat() if p.date else "",
                                "body": p.normalized_body[:4000], "headers": {k: v for k, v in p.headers.items()
                                if k in ("list-unsubscribe", "list-id", "precedence", "auto-submitted", "x-spam-flag")}})
                snippet = re.sub(r"\s+", " ", p.normalized_body)[:300]
                rows.append({"id": sid, "mailbox": box, "from": p.sender[:80], "subject": p.subject[:150],
                             "snippet": snippet, "mail_category": cat, "your_label": "", "notes": ""})
    _write_private(OUT / "golden_sample.jsonl", "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples))
    buf = [",".join(rows[0].keys())] if rows else []
    import io
    sio = io.StringIO()
    w = csv.DictWriter(sio, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    _write_private(OUT / "golden_labels.csv", sio.getvalue())
    print(json.dumps({"samples": len(samples), "per_category": dict(collections.Counter(s["mail_category"] for s in samples)),
                      "pool": {c: len(v) for c, v in pool.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
