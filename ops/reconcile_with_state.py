"""Reconcile category folders with mAIl's recorded decisions (state DB). Report-only by default.

For every message in a category folder WITHOUT an $AI_ keyword (i.e. a copy filed by the old
server keyword sieve), look up mAIl's latest decision for its Message-ID in the state DB:
  * agrees      -> add the $AI_<category> keyword (bookkeeping only);
  * disagrees and mAIl chose a noise category -> retag and move to that category's folder
    (spam -> Junk);
  * disagrees and mAIl chose a human category -> left alone (automated senders belong in folders),
  * Booksy (trusted sender) -> left alone (deliberate archive placement);
  * no decision recorded -> left alone, counted.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/reconcile_with_state.py [--apply]
"""
from __future__ import annotations

import argparse
import collections
import email
import json
import re
import sqlite3
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.imap_client import IMAPClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    prefix = settings.imap_tag_prefix
    noise = {c.strip() for c in settings.hybrid_move_categories.split(",") if c.strip()}
    con = sqlite3.connect(f"file:{settings.state_db_path}?mode=ro", uri=True)
    decision: dict[tuple[str, str], str] = {}
    for box, mid, cat in con.execute(
        "select mailbox_id, message_id, category from email_processing_state "
        "where status='processed' and message_id is not null order by updated_at"
    ):
        decision[(box, mid.strip())] = cat
    report = []
    for mb in settings.load_mailboxes():
        home = {"newsletter": mb.imap_newsletter_folder, "offer": mb.imap_offer_folder, "spam": mb.imap_spam_folder,
                "system": mb.imap_system_folder, "billing": mb.imap_billing_folder}
        folder_cat = {v: k for k, v in home.items() if k != "spam"} | {mb.imap_other_folder: "other"}
        row = {"mailbox_id": mb.mailbox_id, "stats": collections.Counter(), "moves": collections.Counter()}
        try:
            with IMAPClient(mb) as client:
                conn = client.connection
                for folder, cat in folder_cat.items():
                    st, _ = conn.select(folder, readonly=True)
                    if st != "OK":
                        continue
                    st, d = conn.uid("search", None, "ALL")
                    uids = d[0].split() if st == "OK" and d and d[0] else []
                    tag_add: dict[str, list[str]] = collections.defaultdict(list)
                    moves: list[tuple[str, str, str]] = []
                    for i in range(0, len(uids), 300):
                        st, rows = conn.uid("fetch", b",".join(uids[i:i + 300]).decode(),
                                            "(UID FLAGS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM)])")
                        for p in rows or []:
                            if not isinstance(p, tuple):
                                continue
                            meta = p[0].decode(errors="replace")
                            if re.search(re.escape(prefix) + r"(?!midless)\w+", meta):
                                continue
                            uid = re.search(r"UID (\d+)", meta).group(1)
                            h = email.message_from_bytes(p[1])
                            mid = (h.get("Message-ID") or "").strip()
                            dom = parseaddr(h.get("From", ""))[1].lower().rsplit("@", 1)[-1]
                            if dom.endswith("booksy.com"):
                                row["stats"]["booksy_skipped"] += 1
                                continue
                            dec = decision.get((mb.mailbox_id, mid))
                            if dec is None:
                                row["stats"]["no_decision"] += 1
                            elif dec == cat:
                                row["stats"]["agrees"] += 1
                                tag_add[prefix + cat].append(uid)
                            elif dec in noise and dec in home:
                                row["stats"]["moved"] += 1
                                row["moves"][f"{cat}->{dec}"] += 1
                                moves.append((uid, dec, home[dec]))
                            else:
                                row["stats"]["human_decision_kept"] += 1
                    if args.apply and (tag_add or moves):
                        conn.select(folder)
                        for tag, us in tag_add.items():
                            for j in range(0, len(us), 300):
                                conn.uid("store", ",".join(us[j:j + 300]), "+FLAGS.SILENT", f"({tag})")
                        moved = []
                        for uid, dec, target in moves:
                            conn.uid("store", uid, "+FLAGS.SILENT", f"({prefix}{dec})")
                            if conn.uid("copy", uid, target)[0] == "OK":
                                moved.append(uid)
                        for j in range(0, len(moved), 200):
                            chunk = ",".join(moved[j:j + 200])
                            if conn.uid("store", chunk, "+FLAGS.SILENT", "(\\Deleted)")[0] == "OK":
                                conn.uid("expunge", chunk)
                        row["stats"]["move_done"] += len(moved)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        report.append(row)
        print(json.dumps(row, ensure_ascii=False, default=dict))
    out = Path(f"logs/reconcile-state-{'apply' if args.apply else 'report'}-{datetime.now():%Y%m%d-%H%M%S}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=dict))
    print("report ->", out)


if __name__ == "__main__":
    main()
