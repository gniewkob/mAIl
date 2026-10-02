"""Move Booksy/own-site booking notifications that were filed as noise back to INBOX.

Report-only unless --apply. Looks only in noise folders (Newsletter, Offer, System, Billing, Junk),
only at mail since --since, only bookings recognised by classifier_v2.is_booking (subject pattern +
own/trusted sender domain). Skips a message whose Message-ID is already in INBOX (no duplicates).
Moves with UID COPY + delete of the source UID; keeps \\Seen; adds $AI_appointment.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/restore_bookings.py [--since 26-Sep-2026] [--apply]
"""
from __future__ import annotations

import argparse
import email
import json
from email.header import decode_header, make_header

from mail_ai_agent.classifier_v2 import is_booking
from mail_ai_agent.config import Settings
from mail_ai_agent.imap_client import IMAPClient
from mail_ai_agent.schemas import ParsedEmail
from mail_ai_agent.sent_index import _quote


def _dec(v: str | None) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--since", default="26-Sep-2026")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    s = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    out = []
    for mb in s.load_mailboxes():
        folders = [mb.imap_newsletter_folder, mb.imap_offer_folder, mb.imap_system_folder,
                   mb.imap_billing_folder, mb.imap_spam_folder]
        with IMAPClient(mb) as client:
            conn = client.connection
            conn.select("INBOX", readonly=True)
            st, d = conn.uid("search", None, "SINCE", args.since)
            inbox_ids: set[str] = set()
            uids = d[0].split() if st == "OK" and d and d[0] else []
            if uids:
                st, rows = conn.uid("fetch", b",".join(uids).decode(), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                for p in rows or []:
                    if isinstance(p, tuple):
                        inbox_ids.add(str(email.message_from_bytes(p[1]).get("Message-ID", "")).strip().lower())
            for folder in folders:
                if conn.select(_quote(folder), readonly=True)[0] != "OK":
                    continue
                st, d = conn.uid("search", None, "SINCE", args.since, "FROM", "booksy.com")
                cand = d[0].split() if st == "OK" and d and d[0] else []
                st2, d2 = conn.uid("search", None, "SINCE", args.since, "SUBJECT", "rezerwac")
                cand = sorted(set(cand) | set(d2[0].split() if st2 == "OK" and d2 and d2[0] else []))
                for uid in cand:
                    u = uid.decode()
                    st, rows = conn.uid("fetch", u, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID)])")
                    hdr = next((p for p in rows or [] if isinstance(p, tuple)), None)
                    if hdr is None:
                        continue
                    msg = email.message_from_bytes(hdr[1])
                    parsed = ParsedEmail(sender=_dec(msg.get("From")), subject=_dec(msg.get("Subject")))
                    if not is_booking(parsed, mb):
                        continue
                    mid = str(msg.get("Message-ID", "")).strip().lower()
                    seen = b"\\Seen" in hdr[0]
                    item = {"mailbox": mb.mailbox_id, "folder": folder, "uid": u, "subject": parsed.subject[:80],
                            "seen": seen, "duplicate_in_inbox": bool(mid) and mid in inbox_ids}
                    out.append(item)
            if args.apply:
                for item in [i for i in out if i["mailbox"] == mb.mailbox_id]:
                    conn.select(_quote(item["folder"]))
                    if item["duplicate_in_inbox"]:
                        item["result"] = "skipped: already in INBOX"
                        continue
                    new_uid = client.copy_message(item["folder"], item["uid"], "INBOX")
                    conn.select("INBOX")
                    if new_uid:
                        flags = "($AI_appointment \\Seen)" if item["seen"] else "($AI_appointment)"
                        conn.uid("store", new_uid, "+FLAGS.SILENT", flags)
                    client.delete_message(item["folder"], item["uid"])
                    item["result"] = f"moved -> INBOX uid {new_uid}"
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"total {len(out)}, apply={args.apply}")


if __name__ == "__main__":
    main()
