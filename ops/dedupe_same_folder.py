"""Remove same-folder duplicates (same Message-ID twice in ONE folder) and INBOX<->category pairs.

Same-folder duplicates appear when the old server sieve (`fileinto :copy`) filed a message into a
category folder AND mAIl later moved the INBOX twin into the same folder. Keeps one copy per
folder (prefers the one with \\Seen, then the lowest UID), merging \\Seen/\\Flagged/\\Answered.
INBOX<->category pairs are delegated to dedupe_to_hybrid.py (run it after this).

Report-only by default.
  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/dedupe_same_folder.py [--apply]
"""
from __future__ import annotations

import argparse
import collections
import json
from datetime import datetime
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.imap_client import IMAPClient

from dedupe_to_hybrid import SYNC_FLAGS, add_flags, delete_uids, read_folder


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    report = []
    for mb in settings.load_mailboxes():
        folders = [mb.imap_source_folder, mb.imap_appointments_folder, mb.imap_questions_folder,
                   mb.imap_complaints_folder, mb.imap_newsletter_folder, mb.imap_offer_folder,
                   mb.imap_other_folder, mb.imap_billing_folder, mb.imap_system_folder,
                   mb.imap_uncertain_folder, mb.imap_spam_folder]
        row = {"mailbox_id": mb.mailbox_id, "dups": {}}
        try:
            with IMAPClient(mb) as client:
                conn = client.connection
                for folder in folders:
                    content = read_folder(conn, folder)
                    groups = collections.defaultdict(list)
                    for uid, (mid, flags) in content.items():
                        if mid:
                            groups[mid].append(uid)
                    to_del, to_flag = [], {}
                    for mid, uids in groups.items():
                        if len(uids) < 2:
                            continue
                        keep = sorted(uids, key=lambda u: ("\\Seen" not in content[u][1], int(u)))[0]
                        merged = set().union(*(content[u][1] for u in uids)) & set(SYNC_FLAGS)
                        missing = merged - content[keep][1]
                        if missing:
                            to_flag[keep] = missing
                        to_del += [u for u in uids if u != keep]
                    if to_del:
                        row["dups"][folder] = len(to_del)
                        if args.apply:
                            add_flags(conn, folder, to_flag)
                            row["dups"][folder + ":deleted"] = delete_uids(conn, folder, sorted(to_del, key=int))
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        report.append(row)
        print(json.dumps(row, ensure_ascii=False))
    out = Path(f"logs/dedupe-same-folder-{'apply' if args.apply else 'report'}-{datetime.now():%Y%m%d-%H%M%S}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("report ->", out)


if __name__ == "__main__":
    main()
