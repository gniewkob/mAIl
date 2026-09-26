"""Re-file INBOX under hybrid rules (report-only by default).

For every INBOX message with exactly one $AI_<category> keyword:
  * noise category (HYBRID_MOVE_CATEGORIES) -> move to its folder (it had no folder copy, so the
    copy_plus_tag -> hybrid migration left it in INBOX);
  * human category (appointment/question/complaint/other) from an automated or bulk sender ->
    retag via the same guard the worker uses, then move (newsletter / system).
Messages with several conflicting $AI_ tags, or none, are left alone and counted.
Move = UID COPY (flags and keywords travel along) + \\Deleted + UID EXPUNGE of that UID only.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/refile_inbox.py [--apply] [--mailbox-id X]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from datetime import datetime
from pathlib import Path

from mail_ai_agent.automated_sender import HUMAN_CATEGORIES, apply_automated_sender_guard
from mail_ai_agent.config import Settings
from mail_ai_agent.email_parser import parse_email
from mail_ai_agent.imap_client import IMAPClient
from mail_ai_agent.schemas import LLMClassification


def _stub(category: str) -> LLMClassification:
    return LLMClassification.model_validate({
        "category": category, "priority": "low", "requires_reply": False, "confidence": 1.0,
        "summary": "refile", "entities": {}, "draft_reply": None, "reasoning_short": "refile",
    })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--mailbox-id", action="append", default=[])
    args = ap.parse_args()
    settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    prefix = settings.imap_tag_prefix
    moving = {c.strip() for c in settings.hybrid_move_categories.split(",") if c.strip()}
    report = []
    for mb in settings.load_mailboxes():
        if args.mailbox_id and mb.mailbox_id not in args.mailbox_id:
            continue
        folder_of = {
            "newsletter": mb.imap_newsletter_folder, "offer": mb.imap_offer_folder,
            "spam": mb.imap_spam_folder, "system": mb.imap_system_folder, "billing": mb.imap_billing_folder,
        }
        row = {"mailbox_id": mb.mailbox_id, "moves": collections.Counter(), "skipped": collections.Counter(),
               "examples": []}
        plan: list[tuple[str, str, str, str]] = []  # uid, old_tag, new_tag, folder
        try:
            with IMAPClient(mb) as client:
                conn = client.connection
                conn.select(mb.imap_source_folder, readonly=True)
                st, data = conn.uid("search", None, "ALL")
                uids = data[0].split() if st == "OK" and data and data[0] else []
                for i in range(0, len(uids), 300):
                    st, rows = conn.uid("fetch", b",".join(uids[i:i + 300]).decode(), "(UID FLAGS BODY.PEEK[HEADER])")
                    for part in rows or []:
                        if not isinstance(part, tuple):
                            continue
                        meta = part[0].decode(errors="replace")
                        uid = re.search(r"UID (\d+)", meta).group(1)
                        tags = sorted({t[len(prefix):] for t in re.findall(re.escape(prefix) + r"\w+", meta)
                                       if "midless" not in t})
                        if len(tags) != 1:
                            row["skipped"]["no_tag" if not tags else "multi_tag"] += 1
                            continue
                        tag = tags[0]
                        new = tag
                        if tag in HUMAN_CATEGORIES:
                            parsed = parse_email(part[1], settings)
                            new = apply_automated_sender_guard(_stub(tag), parsed, settings.automated_sender_keep).category
                            if new == tag:
                                continue
                            if len(row["examples"]) < 6:
                                row["examples"].append(f"{tag}->{new}: {parsed.sender[:40]} | {parsed.subject[:50]}")
                        if new not in moving or new not in folder_of:
                            continue
                        plan.append((uid, tag, new, folder_of[new]))
                        row["moves"][f"{tag}->{new}" if new != tag else tag] += 1
                if args.apply and plan:
                    conn.select(mb.imap_source_folder)
                    done = 0
                    for uid, old, new, folder in plan:
                        if new != old:
                            conn.uid("store", uid, "-FLAGS.SILENT", f"({prefix}{old})")
                            conn.uid("store", uid, "+FLAGS.SILENT", f"({prefix}{new})")
                        st, _ = conn.uid("copy", uid, folder)
                        if st != "OK":
                            row["skipped"]["copy_failed"] += 1
                            continue
                        conn.uid("store", uid, "+FLAGS.SILENT", "(\\Deleted)")
                        st, _ = conn.uid("expunge", uid)
                        done += st == "OK"
                    row["moved"] = done
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["planned"] = len(plan)
        report.append(row)
        print(json.dumps(row, ensure_ascii=False, default=dict))
    out = Path(f"logs/refile-inbox-{'apply' if args.apply else 'report'}-{datetime.now():%Y%m%d-%H%M%S}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=dict))
    print("report ->", out)


if __name__ == "__main__":
    main()
