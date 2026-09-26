"""Apply two high-precision deterministic rules to mail already sitting in the mailbox.

  * bank documents (rule_engine.BANK_DOCUMENT_REGEX on the subject) -> Billing
      scope: INBOX and untagged messages in category folders
  * server-flagged spam (X-Spam-Flag: YES), not from a trusted sender -> Junk
      scope: untagged messages in category folders only (never INBOX: a human already saw those)
Moved messages get the matching $AI_ keyword. Report-only without --apply.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/backfill_strict_rules.py [--apply]
"""
from __future__ import annotations

import argparse
import collections
import email
import json
import re
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.imap_client import IMAPClient
from mail_ai_agent.rule_engine import BANK_DOCUMENT_REGEX, REPLY_PREFIX_REGEX
from mail_ai_agent.sieve_unify_cli import trusted_domains_from


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    s = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    prefix = s.imap_tag_prefix
    trusted = trusted_domains_from(s.automated_sender_keep)
    report = []
    for mb in s.load_mailboxes():
        row = {"mailbox_id": mb.mailbox_id, "moves": collections.Counter(), "examples": []}
        folders = [mb.imap_source_folder, mb.imap_newsletter_folder, mb.imap_offer_folder,
                   mb.imap_system_folder, mb.imap_other_folder, mb.imap_billing_folder]
        try:
            with IMAPClient(mb) as client:
                conn = client.connection
                for folder in folders:
                    if conn.select(folder, readonly=True)[0] != "OK":
                        continue
                    st, d = conn.uid("search", None, "ALL")
                    uids = d[0].split() if st == "OK" and d and d[0] else []
                    plan: list[tuple[str, str, str]] = []
                    for i in range(0, len(uids), 300):
                        st, rows = conn.uid("fetch", b",".join(uids[i:i + 300]).decode(),
                                            "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT X-SPAM-FLAG)])")
                        for p in rows or []:
                            if not isinstance(p, tuple):
                                continue
                            meta = p[0].decode(errors="replace")
                            uid = re.search(r"UID (\d+)", meta).group(1)
                            tagged = bool(re.search(re.escape(prefix) + r"(?!midless)\w+", meta))
                            h = email.message_from_bytes(p[1])
                            try:
                                subj = str(make_header(decode_header(h.get("Subject", ""))))
                            except Exception:
                                subj = h.get("Subject", "") or ""
                            dom = parseaddr(h.get("From", ""))[1].lower().rsplit("@", 1)[-1]
                            in_inbox = folder == mb.imap_source_folder
                            if (in_inbox or not tagged) and folder != mb.imap_billing_folder and BANK_DOCUMENT_REGEX.search(subj) and not REPLY_PREFIX_REGEX.match(subj):
                                plan.append((uid, "billing", mb.imap_billing_folder))
                            elif (not in_inbox and not tagged
                                  and (h.get("X-Spam-Flag") or "").strip().upper().startswith("YES")
                                  and not any(dom == t or dom.endswith("." + t) for t in trusted)):
                                plan.append((uid, "spam", mb.imap_spam_folder))
                            else:
                                continue
                            row["moves"][f"{folder.replace('INBOX.', '')}->{plan[-1][1]}"] += 1
                            if len(row["examples"]) < 4:
                                row["examples"].append(f"{plan[-1][1]}: {subj[:60]}")
                    if args.apply and plan:
                        conn.select(folder)
                        moved = []
                        for uid, cat, target in plan:
                            old = re.findall(re.escape(prefix) + r"(?!midless)\w+", "")
                            conn.uid("store", uid, "+FLAGS.SILENT", f"({prefix}{cat})")
                            if conn.uid("copy", uid, target)[0] == "OK":
                                moved.append(uid)
                        for j in range(0, len(moved), 200):
                            chunk = ",".join(moved[j:j + 200])
                            if conn.uid("store", chunk, "+FLAGS.SILENT", "(\\Deleted)")[0] == "OK":
                                conn.uid("expunge", chunk)
                        row["moves"]["done"] += len(moved)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        report.append(row)
        print(json.dumps(row, ensure_ascii=False, default=dict))
    out = Path(f"logs/backfill-strict-{'apply' if args.apply else 'report'}-{datetime.now():%Y%m%d-%H%M%S}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    main()
