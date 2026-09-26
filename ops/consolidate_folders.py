"""One copy per message across ALL folders + Booksy booking placement (report-only by default).

Rules, per Message-ID (all copies in INBOX, category folders and Junk):
 1. Booksy booking notification (From @booksy.com, subject about a reservation):
      target = INBOX (tag $AI_appointment) if the visit date in the subject is today or later,
      or (no parsable date) it arrived within 14 days; otherwise INBOX.System (archive).
 2. Otherwise, only messages present in 2+ different folders are touched:
      a. a Junk copy WITHOUT the server's `X-Spam-Flag: YES` = user/SpamSieve decision -> keep Junk;
      b. exactly one $AI_<category> across copies = mAIl's decision -> its hybrid home
         (human/uncertain -> INBOX, noise -> category folder);
      c. otherwise keep by priority: INBOX > Billing > System > Offer > Newsletter > Other > Junk.
 Kept copy gets \\Seen/\\Flagged/\\Answered merged from all copies. If no copy sits in the target,
 one is COPYed there first. Other copies are deleted with UID EXPUNGE (only those UIDs).

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/consolidate_folders.py [--apply] [--mailbox-id X]
"""
from __future__ import annotations

import argparse
import collections
import email
import json
import re
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path

from mail_ai_agent.automated_sender import HUMAN_CATEGORIES
from mail_ai_agent.config import Settings
from mail_ai_agent.imap_client import IMAPClient

SYNC = ("\\Seen", "\\Flagged", "\\Answered")
MONTHS = {m: i + 1 for i, m in enumerate(
    "stycznia lutego marca kwietnia maja czerwca lipca sierpnia września października listopada grudnia".split())}
BOOKING = re.compile(r"rezerwac|wizyt|odwoła|anulow|termin", re.I)


def visit_date(subject: str) -> date | None:
    m = re.search(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", subject, re.I)
    if not m:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        return None


def read(conn, folder: str) -> dict[str, dict]:
    st, _ = conn.select(folder, readonly=True)
    if st != "OK":
        return {}
    st, d = conn.uid("search", None, "ALL")
    uids = d[0].split() if st == "OK" and d and d[0] else []
    out: dict[str, dict] = {}
    for i in range(0, len(uids), 300):
        st, rows = conn.uid("fetch", b",".join(uids[i:i + 300]).decode(),
                            "(UID FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT X-SPAM-FLAG)])")
        for p in rows or []:
            if not isinstance(p, tuple):
                continue
            meta = p[0].decode(errors="replace")
            uid = re.search(r"UID (\d+)", meta)
            fl = re.search(r"FLAGS \(([^)]*)\)", meta)
            dt = re.search(r'INTERNALDATE "([^"]+)"', meta)
            h = email.message_from_bytes(p[1])
            mid = (h.get("Message-ID") or "").strip()
            if not uid or not mid:
                continue
            try:
                subj = str(make_header(decode_header(h.get("Subject", ""))))
            except Exception:
                subj = h.get("Subject", "") or ""
            out[uid.group(1)] = {
                "mid": mid, "flags": set(fl.group(1).split()) if fl else set(),
                "domain": parseaddr(h.get("From", ""))[1].lower().rsplit("@", 1)[-1],
                "subject": subj, "server_spam": (h.get("X-Spam-Flag") or "").strip().upper().startswith("YES"),
                "arrived": datetime.strptime(dt.group(1), "%d-%b-%Y %H:%M:%S %z") if dt else None,
            }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--mailbox-id", action="append", default=[])
    args = ap.parse_args()
    settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    prefix = settings.imap_tag_prefix
    moving = {c.strip() for c in settings.hybrid_move_categories.split(",") if c.strip()}
    today = datetime.now().date()
    now = datetime.now(timezone.utc)
    report = []
    for mb in settings.load_mailboxes():
        if args.mailbox_id and mb.mailbox_id not in args.mailbox_id:
            continue
        home = {"newsletter": mb.imap_newsletter_folder, "offer": mb.imap_offer_folder, "spam": mb.imap_spam_folder,
                "system": mb.imap_system_folder, "billing": mb.imap_billing_folder}
        priority = [mb.imap_source_folder, mb.imap_billing_folder, mb.imap_system_folder, mb.imap_offer_folder,
                    mb.imap_newsletter_folder, mb.imap_other_folder, mb.imap_spam_folder]
        folders = [mb.imap_source_folder, mb.imap_appointments_folder, mb.imap_questions_folder,
                   mb.imap_complaints_folder, mb.imap_newsletter_folder, mb.imap_offer_folder, mb.imap_other_folder,
                   mb.imap_billing_folder, mb.imap_system_folder, mb.imap_uncertain_folder, mb.imap_spam_folder]
        row = {"mailbox_id": mb.mailbox_id, "stats": collections.Counter(), "examples": []}
        try:
            with IMAPClient(mb) as client:
                conn = client.connection
                copies: dict[str, list[tuple[str, str, dict]]] = collections.defaultdict(list)
                for f in folders:
                    for uid, info in read(conn, f).items():
                        copies[info["mid"]].append((f, uid, info))
                # plan: folder -> {"flags": {uid: set}, "retag": {uid: (remove, add)}, "copy": [(uid, target)], "delete": [uid]}
                plan = collections.defaultdict(lambda: {"flags": {}, "retag": {}, "copy": [], "delete": []})
                for mid, cs in copies.items():
                    info0 = cs[0][2]
                    folders_here = {f for f, _, _ in cs}
                    tag_to_set = None
                    is_booking = info0["domain"].endswith("booksy.com") and BOOKING.search(info0["subject"] or "")
                    if is_booking:
                        vd = visit_date(info0["subject"])
                        recent = bool(info0["arrived"] and now - info0["arrived"] <= timedelta(days=14))
                        upcoming = vd >= today if vd else recent
                        target = mb.imap_source_folder if upcoming else mb.imap_system_folder
                        tag_to_set = "appointment"
                        kind = "booksy_inbox" if upcoming else "booksy_archive"
                        if folders_here == {target} and len(cs) == 1:
                            continue
                    elif len(folders_here) < 2:
                        continue
                    else:
                        user_junk = [c for c in cs if c[0] == mb.imap_spam_folder and not c[2]["server_spam"]]
                        tags = {t[len(prefix):] for _, _, i in cs for t in i["flags"]
                                if t.startswith(prefix) and "midless" not in t}
                        server_junk = [c for c in cs if c[0] == mb.imap_spam_folder and c[2]["server_spam"]]
                        if user_junk:
                            target, kind = mb.imap_spam_folder, "keep_user_junk"
                        elif server_junk and (not tags or "spam" in tags):
                            # no clear mAIl decision: the server spam score beats the old keyword sieve
                            # copies (the same rule new mail follows since the sieve change)
                            target, kind = mb.imap_spam_folder, "keep_server_junk"
                        elif len(tags) == 1:
                            cat = next(iter(tags))
                            target = home[cat] if cat in moving and cat in home else mb.imap_source_folder
                            kind = "keep_mail_decision"
                        else:
                            target = next((f for f in priority if f in folders_here), cs[0][0])
                            kind = "keep_by_priority"
                    merged = set().union(*(c[2]["flags"] for c in cs)) & set(SYNC)
                    in_target = sorted([c for c in cs if c[0] == target], key=lambda c: ("\\Seen" not in c[2]["flags"], int(c[1])))
                    if in_target:
                        keep = in_target[0]
                        missing = merged - keep[2]["flags"]
                        if missing:
                            plan[keep[0]]["flags"][keep[1]] = missing
                        if tag_to_set:
                            old = {t for t in keep[2]["flags"] if t.startswith(prefix) and "midless" not in t}
                            if old != {prefix + tag_to_set}:
                                plan[keep[0]]["retag"][keep[1]] = (old - {prefix + tag_to_set}, prefix + tag_to_set)
                        others = [c for c in cs if c is not keep]
                    else:
                        src = cs[0]
                        missing = merged - src[2]["flags"]
                        if missing:
                            plan[src[0]]["flags"][src[1]] = missing
                        if tag_to_set:
                            old = {t for t in src[2]["flags"] if t.startswith(prefix) and "midless" not in t}
                            plan[src[0]]["retag"][src[1]] = (old - {prefix + tag_to_set}, prefix + tag_to_set)
                        plan[src[0]]["copy"].append((src[1], target))
                        others = list(cs)  # source is deleted after it was copied
                    for f, uid, _ in others:
                        plan[f]["delete"].append(uid)
                    row["stats"][kind] += 1
                    row["stats"]["copies_removed"] += len(others) - (0 if in_target else 1)
                    if len(row["examples"]) < 8 and (is_booking or kind != "keep_mail_decision"):
                        row["examples"].append(f"{kind}: {sorted(f.replace('INBOX.', '') for f in folders_here)} -> "
                                               f"{target.replace('INBOX.', '')} | {info0['subject'][:55]}")
                if args.apply:
                    done = collections.Counter()
                    # 1) flags + retags + copies (source folders), 2) deletes
                    for f, p in plan.items():
                        if not (p["flags"] or p["retag"] or p["copy"]):
                            continue
                        conn.select(f)
                        for uid, fl in p["flags"].items():
                            conn.uid("store", uid, "+FLAGS.SILENT", f"({' '.join(sorted(fl))})")
                        for uid, (remove, add) in p["retag"].items():
                            if remove:
                                conn.uid("store", uid, "-FLAGS.SILENT", f"({' '.join(sorted(remove))})")
                            conn.uid("store", uid, "+FLAGS.SILENT", f"({add})")
                        for uid, target in p["copy"]:
                            st, _ = conn.uid("copy", uid, target)
                            if st == "OK":
                                done["copied"] += 1
                            else:
                                p["delete"].remove(uid)  # never delete a source that was not copied
                                done["copy_failed"] += 1
                    for f, p in plan.items():
                        if not p["delete"]:
                            continue
                        conn.select(f)
                        uids = sorted(set(p["delete"]), key=int)
                        for i in range(0, len(uids), 200):
                            chunk = ",".join(uids[i:i + 200])
                            st, _ = conn.uid("store", chunk, "+FLAGS.SILENT", "(\\Deleted)")
                            if st == "OK" and conn.uid("expunge", chunk)[0] == "OK":
                                done["deleted"] += len(uids[i:i + 200])
                    row["applied"] = dict(done)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        report.append(row)
        print(json.dumps(row, ensure_ascii=False, default=dict))
    out = Path(f"logs/consolidate-{'apply' if args.apply else 'report'}-{datetime.now():%Y%m%d-%H%M%S}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=dict))
    print("report ->", out)


if __name__ == "__main__":
    main()
