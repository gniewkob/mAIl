"""Re-file online-shop mail already sorted by mAIl according to rule_engine.retail_decision.

Scans only mAIl's own folders (Newsletter, Offer, Billing, System) for mail from RETAIL_DOMAINS and
moves it where the retail rule says: marketing -> Newsletter, order/shipment -> Zakupy, order needing
action -> INBOX. Never touches INBOX, Junk or Trash (owner's own decisions). Retags $AI_<category>,
keeps \\Seen. Report-only unless --apply.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/refile_retail.py [--since 18-Sep-2026] [--apply]
"""
from __future__ import annotations

import argparse
import collections
import email
import re
from email.header import decode_header, make_header

from mail_ai_agent.config import Settings
from mail_ai_agent.folder_mapper import category_to_folder
from mail_ai_agent.imap_client import IMAPClient
from mail_ai_agent.rule_engine import RETAIL_DOMAINS, retail_decision
from mail_ai_agent.schemas import ParsedEmail


def _dec(v: str | None) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--since", default="18-Sep-2026")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    s = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    prefix = s.imap_tag_prefix
    total = collections.Counter()
    for mb in s.load_mailboxes():
        sources = [mb.imap_newsletter_folder, mb.imap_offer_folder, mb.imap_billing_folder, mb.imap_system_folder]
        plan = []
        with IMAPClient(mb) as client:
            conn = client.connection
            for folder in sources:
                if conn.select(f'"{folder}"', readonly=True)[0] != "OK":
                    continue
                uids: set[bytes] = set()
                for dom in RETAIL_DOMAINS:
                    st, d = conn.uid("search", None, "SINCE", args.since, "FROM", dom)
                    uids |= set(d[0].split()) if st == "OK" and d and d[0] else set()
                for uid in sorted(uids, key=int):
                    u = uid.decode()
                    st, rows = conn.uid("fetch", u, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                    part = next((p for p in rows or [] if isinstance(p, tuple)), None)
                    if part is None:
                        continue
                    msg = email.message_from_bytes(part[1])
                    parsed = ParsedEmail(sender=_dec(msg.get("From")), subject=_dec(msg.get("Subject")))
                    decision = retail_decision(parsed)
                    if decision is None:
                        continue
                    category = decision[0]
                    target = mb.imap_source_folder if category == "question" else category_to_folder(category, mb)
                    if target == folder:
                        continue
                    flags = re.findall(r"\$AI_\w+|\\Seen|\\Flagged", part[0].decode(errors="replace"))
                    plan.append((folder, u, target, category, parsed.subject[:60], flags))
            for folder, u, target, category, subject, flags in plan:
                total[(mb.mailbox_id, folder, target)] += 1
                if not args.apply:
                    continue
                conn.select(f'"{folder}"')
                new_uid = client.copy_message(folder, u, target)
                if new_uid:
                    conn.select(f'"{target}"')
                    old_tags = [f for f in flags if f.startswith(prefix)]
                    if old_tags:
                        conn.uid("store", new_uid, "-FLAGS.SILENT", f"({' '.join(old_tags)})")
                    keep = [f for f in flags if f in ("\\Seen", "\\Flagged")]
                    conn.uid("store", new_uid, "+FLAGS.SILENT", f"({' '.join([prefix + category] + keep)})")
                client.delete_message(folder, u)
        for folder, u, target, category, subject, flags in plan[:6]:
            print(f"  {mb.mailbox_id:18} {folder:17} -> {target:16} {subject}")
    for k, v in sorted(total.items()):
        print(v, k)
    print(f"total {sum(total.values())}, apply={args.apply}")


if __name__ == "__main__":
    main()
