"""Move mail that mAIl itself filed in Junk (tag $AI_spam, server X-Spam-Flag not YES) back out.

Mail the owner or SpamSieve moved to Junk carries no $AI_ keyword and is never touched.
Default target INBOX.Newsletter (retag $AI_newsletter); subjects matching --to-inbox go to INBOX
($AI_question). Keeps \\Seen. Report-only unless --apply.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/release_model_junk.py \
      --mailbox gniewko_bodora --since 09-Oct-2026 --to-inbox "oczekiwanie na potwierdzenie|ubezpieczenie" [--apply]
"""
from __future__ import annotations

import argparse
import email
import re
from email.header import decode_header, make_header

from mail_ai_agent.config import Settings
from mail_ai_agent.imap_client import IMAPClient


def _dec(v: str | None) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--mailbox", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--to-inbox", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    s = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    prefix = s.imap_tag_prefix
    to_inbox = re.compile(args.to_inbox, re.IGNORECASE) if args.to_inbox else None
    mb = next(m for m in s.load_mailboxes() if m.mailbox_id == args.mailbox)
    plan = []
    with IMAPClient(mb) as client:
        conn = client.connection
        junk = f'"{mb.imap_spam_folder}"'
        conn.select(junk, readonly=True)
        st, d = conn.uid("search", None, "SINCE", args.since, "KEYWORD", f"{prefix}spam")
        for uid in (d[0].split() if st == "OK" and d and d[0] else []):
            u = uid.decode()
            st, rows = conn.uid("fetch", u, "(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT X-SPAM-FLAG)])")
            part = next((p for p in rows or [] if isinstance(p, tuple)), None)
            if part is None:
                continue
            msg = email.message_from_bytes(part[1])
            if str(msg.get("X-Spam-Flag", "")).strip().upper().startswith("YES"):
                continue  # the server's own verdict stays in Junk
            subject = _dec(msg.get("Subject"))
            inbox = bool(to_inbox and to_inbox.search(subject))
            plan.append({"uid": u, "subject": subject[:70], "seen": b"\\Seen" in part[0],
                         "target": "INBOX" if inbox else mb.imap_newsletter_folder,
                         "tag": f"{prefix}question" if inbox else f"{prefix}newsletter"})
        for p in plan:
            print(f"{'->' if args.apply else '  '} {p['target']:18} {p['subject']}")
            if not args.apply:
                continue
            conn.select(junk)
            new_uid = client.copy_message(mb.imap_spam_folder, p["uid"], p["target"])
            if new_uid:
                conn.select(f'"{p["target"]}"')
                conn.uid("store", new_uid, "-FLAGS.SILENT", f"({prefix}spam)")
                conn.uid("store", new_uid, "+FLAGS.SILENT", f"({p['tag']}{' \\Seen' if p['seen'] else ''})")
            client.delete_message(mb.imap_spam_folder, p["uid"])
    print(f"total {len(plan)}, apply={args.apply}")


if __name__ == "__main__":
    main()
