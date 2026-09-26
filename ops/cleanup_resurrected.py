"""Find mail that mAIl's nightly `repair` resurrected into INBOX (read-only by default).

A message is "resurrected" when it sits in INBOX while the same Message-ID is also in
Junk (user/SpamSieve/server marked it spam) or in Trash (user deleted it).
Also reports literal duplicates inside INBOX (same Message-ID, several UIDs).

Default: report only (EXAMINE / BODY.PEEK, nothing changes).
--apply: removes only the INBOX copies of resurrected messages (the user's copy in Junk/Trash
stays untouched) and the extra INBOX duplicates (keeps one, preferring a \\Seen copy).

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/cleanup_resurrected.py [--apply] [--mailbox-id X]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.imap_client import IMAPClient

TRASH_NAMES = ("trash", "kosz", "deleted", "deleted messages", "deleted items")


def find_trash(client: IMAPClient) -> list[str]:
    conn = client.connection
    status, data = conn.list()
    out = []
    for raw in data or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        leaf = re.split(r"[./]", name)[-1].lower()
        if "\\Trash" in m.group("flags") or leaf in TRASH_NAMES:
            out.append(name)
    return out


def msgids_and_flags(client: IMAPClient, folder: str) -> dict[str, tuple[str | None, bool]]:
    conn = client.connection
    status, _ = conn.select(f'"{folder}"' if " " in folder else folder, readonly=True)
    if status != "OK":
        return {}
    status, data = conn.uid("search", None, "ALL")
    uids = data[0].split() if status == "OK" and data and data[0] else []
    out: dict[str, tuple[str | None, bool]] = {}
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
            seen = "\\Seen" in meta
            hdr = part[1].decode(errors="replace")
            m_id = re.search(r"Message-ID:\s*(<[^>]+>)", hdr, re.I)
            out[m_uid.group(1)] = (m_id.group(1).strip() if m_id else None, seen)
    return out


def worker_seen_inbox_uids(db: Path) -> dict[str, set[str]]:
    """UIDs the worker itself processed in INBOX: originals, not copies restored by `repair`."""
    import sqlite3

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: dict[str, set[str]] = collections.defaultdict(set)
    for box, uid in con.execute(
        "select mailbox_id, imap_uid from email_processing_state where source_folder='INBOX' and imap_uid is not null"
    ):
        out[box].add(str(uid))
    con.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--mailbox-id", action="append", default=[])
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    boxes = [m for m in settings.load_mailboxes() if not args.mailbox_id or m.mailbox_id in args.mailbox_id]
    report = {"apply": args.apply, "generated_at": datetime.now(timezone.utc).isoformat(), "mailboxes": []}
    worker_uids = worker_seen_inbox_uids(Path(settings.state_db_path))

    for mb in boxes:
        row: dict = {"mailbox_id": mb.mailbox_id}
        try:
            with IMAPClient(mb) as client:
                trash = find_trash(client)
                inbox = msgids_and_flags(client, mb.imap_source_folder)
                junk_ids = {mid for mid, _ in msgids_and_flags(client, mb.imap_spam_folder).values() if mid}
                trash_ids: set[str] = set()
                for t in trash:
                    trash_ids |= {mid for mid, _ in msgids_and_flags(client, t).values() if mid}

                by_mid: dict[str, list[str]] = collections.defaultdict(list)
                for uid, (mid, _) in inbox.items():
                    if mid:
                        by_mid[mid].append(uid)

                in_junk_all = [u for u, (mid, _) in inbox.items() if mid and mid in junk_ids]
                originals = worker_uids.get(mb.imap_source_folder == "INBOX" and mb.mailbox_id or "", set())
                # Originals the worker classified as spam: it copied them to Junk and kept them in INBOX
                # by design (copy_plus_tag). Not resurrected -> reported, never removed here.
                spam_originals = [u for u in in_junk_all if u in originals]
                in_junk = [u for u in in_junk_all if u not in originals]
                in_trash = [u for u, (mid, _) in inbox.items() if mid and mid in trash_ids and mid not in junk_ids]
                resurrected = set(in_junk) | set(in_trash)
                dup_extra: list[str] = []
                for mid, uids in by_mid.items():
                    keep_pool = [u for u in uids if u not in resurrected and u not in spam_originals]
                    if len(keep_pool) > 1:
                        keep = sorted(keep_pool, key=lambda u: (not inbox[u][1], int(u)))[0]
                        dup_extra += [u for u in keep_pool if u != keep]

                row.update({
                    "trash_folders": trash,
                    "inbox_total": len(inbox),
                    "inbox_unseen": sum(1 for _, s in inbox.values() if not s),
                    "resurrected_from_junk": len(in_junk),
                    "spam_originals_kept_in_inbox_by_worker": len(spam_originals),
                    "resurrected_from_trash": len(in_trash),
                    "resurrected_unseen": sum(1 for u in resurrected if not inbox[u][1]),
                    "inbox_duplicates_extra": len(dup_extra),
                    "to_remove_uids": sorted(resurrected | set(dup_extra), key=int),
                })

                if args.apply and row["to_remove_uids"]:
                    conn = client.connection
                    conn.select(mb.imap_source_folder)  # read-write only when applying
                    removed = 0
                    for i in range(0, len(row["to_remove_uids"]), 200):
                        chunk = ",".join(row["to_remove_uids"][i:i + 200])
                        st, _ = conn.uid("store", chunk, "+FLAGS.SILENT", "(\\Deleted)")
                        if st == "OK":
                            conn.uid("expunge", chunk)  # UIDPLUS: expunges only these UIDs
                            removed += chunk.count(",") + 1
                    row["removed"] = removed
        except Exception as exc:  # report and continue with other mailboxes
            row["error"] = f"{type(exc).__name__}: {exc}"
        report["mailboxes"].append(row)
        slim = {k: v for k, v in row.items() if k != "to_remove_uids"}
        print(json.dumps(slim, ensure_ascii=False))

    out = Path(args.output or f"logs/cleanup-resurrected-{'apply' if args.apply else 'report'}-"
                              f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("report ->", out)


if __name__ == "__main__":
    main()
