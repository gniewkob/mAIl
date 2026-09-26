"""One-off migration of a mailbox from copy_plus_tag (two copies) to hybrid (one copy).

For every message present both in INBOX and in a category folder:
  * noise category (HYBRID_MOVE_CATEGORIES, default newsletter,offer,spam,system,billing):
      keep the category-folder copy, give it the INBOX copy's \\Seen/\\Flagged/\\Answered, delete the INBOX copy
  * any other category (appointment, question, complaint, other, uncertain):
      keep the INBOX original, give it the folder copy's flags, delete the folder copy
Category-folder copies whose INBOX original is gone (the user deleted it) are deleted as well.
Junk: only copies that still have an INBOX twin are touched (the INBOX twin is removed);
Junk-only mail is never touched.

Default is report-only. Guard: refuses a mailbox whose INBOX listing is empty.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/dedupe_to_hybrid.py --mailbox-id X [--apply]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from datetime import datetime
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.imap_client import IMAPClient

SYNC_FLAGS = ("\\Seen", "\\Flagged", "\\Answered")


def read_folder(conn, folder: str) -> dict[str, tuple[str | None, set[str]]]:
    status, _ = conn.select(folder, readonly=True)
    if status != "OK":
        return {}
    status, data = conn.uid("search", None, "ALL")
    uids = data[0].split() if status == "OK" and data and data[0] else []
    out: dict[str, tuple[str | None, set[str]]] = {}
    for i in range(0, len(uids), 300):
        chunk = b",".join(uids[i:i + 300]).decode()
        status, rows = conn.uid("fetch", chunk, "(UID FLAGS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        for part in rows or []:
            if not isinstance(part, tuple):
                continue
            meta = part[0].decode(errors="replace")
            m_uid = re.search(r"UID (\d+)", meta)
            if not m_uid:
                continue
            m_flags = re.search(r"FLAGS \(([^)]*)\)", meta)
            flags = set(m_flags.group(1).split()) if m_flags else set()
            m_id = re.search(r"Message-ID:\s*(<[^>]+>)", part[1].decode(errors="replace"), re.I)
            out[m_uid.group(1)] = (m_id.group(1).strip() if m_id else None, flags)
    return out


def add_flags(conn, folder: str, plan: dict[str, set[str]]) -> int:
    if not plan:
        return 0
    conn.select(folder)
    by_flagset: dict[tuple[str, ...], list[str]] = collections.defaultdict(list)
    for uid, flags in plan.items():
        by_flagset[tuple(sorted(flags))].append(uid)
    n = 0
    for flags, uids in by_flagset.items():
        for i in range(0, len(uids), 200):
            st, _ = conn.uid("store", ",".join(uids[i:i + 200]), "+FLAGS.SILENT", f"({' '.join(flags)})")
            if st == "OK":
                n += len(uids[i:i + 200])
    return n


def delete_uids(conn, folder: str, uids: list[str]) -> int:
    if not uids:
        return 0
    conn.select(folder)
    n = 0
    for i in range(0, len(uids), 200):
        chunk = ",".join(uids[i:i + 200])
        st, _ = conn.uid("store", chunk, "+FLAGS.SILENT", "(\\Deleted)")
        if st == "OK":
            st2, _ = conn.uid("expunge", chunk)  # UIDPLUS: only these UIDs
            if st2 == "OK":
                n += len(uids[i:i + 200])
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--mailbox-id", action="append", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    move_cats = {c.strip() for c in settings.hybrid_move_categories.split(",") if c.strip()}
    report = {"apply": args.apply, "mailboxes": []}
    for mb in [m for m in settings.load_mailboxes() if m.mailbox_id in args.mailbox_id]:
        folders = {
            mb.imap_appointments_folder: "appointment", mb.imap_questions_folder: "question",
            mb.imap_complaints_folder: "complaint", mb.imap_newsletter_folder: "newsletter",
            mb.imap_offer_folder: "offer", mb.imap_other_folder: "other", mb.imap_billing_folder: "billing",
            mb.imap_system_folder: "system", mb.imap_uncertain_folder: "uncertain", mb.imap_spam_folder: "spam",
        }
        row: dict = {"mailbox_id": mb.mailbox_id, "per_folder": {}}
        try:
            with IMAPClient(mb) as client:
                conn = client.connection
                inbox = read_folder(conn, mb.imap_source_folder)
                if not inbox:
                    raise RuntimeError("guard: INBOX listing empty, refusing")
                by_mid: dict[str, list[str]] = collections.defaultdict(list)
                for uid, (mid, _) in inbox.items():
                    if mid:
                        by_mid[mid].append(uid)
                inbox_delete: set[str] = set()
                inbox_flags: dict[str, set[str]] = collections.defaultdict(set)
                for folder, cat in folders.items():
                    content = read_folder(conn, folder)
                    fdel: list[str] = []
                    fflags: dict[str, set[str]] = {}
                    stats = collections.Counter()
                    for uid, (mid, flags) in content.items():
                        twins = by_mid.get(mid, []) if mid else []
                        if not twins:
                            if cat == "spam" or cat in move_cats or not mid:
                                # Junk-only, no Message-ID, or a noise message whose single home is this
                                # folder (hybrid): never touched. Makes re-runs on a hybrid mailbox safe.
                                stats["kept_single"] += 1
                                continue
                            fdel.append(uid)
                            stats["orphan_deleted"] += 1
                            continue
                        twin_flags = set().union(*(inbox[t][1] for t in twins))
                        if cat in move_cats:
                            missing = {f for f in SYNC_FLAGS if f in twin_flags and f not in flags}
                            if missing:
                                fflags[uid] = missing
                            inbox_delete.update(twins)
                            stats["pair_keep_folder"] += 1
                        else:
                            for t in twins:
                                missing = {f for f in SYNC_FLAGS if f in flags and f not in inbox[t][1]}
                                if missing:
                                    inbox_flags[t] |= missing
                            fdel.append(uid)
                            stats["pair_keep_inbox"] += 1
                    if args.apply:
                        stats["flags_synced"] = add_flags(conn, folder, fflags)
                        stats["deleted"] = delete_uids(conn, folder, fdel)
                    row["per_folder"][folder] = dict(stats)
                row["inbox_copies_to_delete"] = len(inbox_delete)
                if args.apply:
                    row["inbox_flags_synced"] = add_flags(conn, mb.imap_source_folder, dict(inbox_flags))
                    row["inbox_deleted"] = delete_uids(conn, mb.imap_source_folder, sorted(inbox_delete, key=int))
                row["inbox_before"] = len(inbox)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        report["mailboxes"].append(row)
        print(json.dumps(row, ensure_ascii=False, indent=1))
    out = Path(f"logs/dedupe-to-hybrid-{'apply' if args.apply else 'report'}-{datetime.now():%Y%m%d-%H%M%S}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("report ->", out)


if __name__ == "__main__":
    main()
