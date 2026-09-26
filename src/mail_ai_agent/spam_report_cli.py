"""Report spam that the server filter missed to mydevil, so its SpamAssassin learns.

mydevil learns only from explicit reports ("Oznacz jako Spam" in Roundcube, or the message
forwarded as an attachment to spam@mydevil.net). This job forwards, per mailbox, messages that sit
in the spam folder (put there by the user, SpamSieve or mAIl) but were NOT flagged by the server
(no `X-Spam-Flag: YES`), i.e. the server's false negatives.

Safety:
  * grace period: only messages that have been in the mailbox >= --min-age-hours (default 24 h),
    so a false positive can still be rescued from Junk before it is reported;
  * look-back window --max-age-days (default 8), per-run cap --max-per-mailbox (default 40),
    messages larger than --max-bytes (default 1 MB) are skipped;
  * each message is reported once (sha256 of Message-ID stored in the state file, no raw IDs);
  * IMAP is read-only (EXAMINE + BODY.PEEK); nothing in the mailbox changes;
  * default is a dry run; --apply sends.
Sent from each mailbox's own account via the provider's SMTP (STARTTLS 587).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from .config import Settings
from .imap_client import IMAPClient
from .sieve_unify_cli import trusted_domains_from

REPORT_TO = "spam@mydevil.net"
BATCH = 10


def _sha(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _load_state(path: Path) -> dict[str, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(path: Path, state: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({k: v[-20000:] for k, v in state.items()}, fh)
    tmp.replace(path)


def is_server_flagged(raw_headers: bytes) -> bool:
    msg = message_from_bytes(raw_headers)
    return any(str(v).strip().upper().startswith("YES") for v in msg.get_all("X-Spam-Flag", []))


def select_candidates(
    items: list[tuple[str, str, int, bytes]],  # uid, internaldate(RFC2822), size, header bytes
    reported: set[str],
    *,
    now: datetime,
    min_age_hours: float,
    max_age_days: float,
    max_bytes: int,
    limit: int,
    trusted_domains: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """Return [(uid, message_id_sha)] eligible for reporting, oldest first."""
    out: list[tuple[datetime, str, str]] = []
    for uid, internaldate, size, headers in items:
        if size > max_bytes or is_server_flagged(headers):
            continue
        parsed_headers = message_from_bytes(headers)
        domain = parseaddr(parsed_headers.get("From", ""))[1].lower().rsplit("@", 1)[-1]
        if any(domain == d or domain.endswith("." + d) for d in trusted_domains):
            continue  # never teach the server that a trusted sender (booking system) is spam
        mid = (parsed_headers.get("Message-ID") or "").strip()
        if not mid:
            continue
        key = _sha(mid)
        if key in reported:
            continue
        try:
            arrived = parsedate_to_datetime(internaldate)
        except (TypeError, ValueError):
            continue
        age = now - arrived
        if timedelta(hours=min_age_hours) <= age <= timedelta(days=max_age_days):
            out.append((arrived, uid, key))
    out.sort()
    return [(uid, key) for _, uid, key in out[:limit]]


def _fetch_items(conn, folder: str) -> list[tuple[str, str, int, bytes]]:
    status, _ = conn.select(folder, readonly=True)
    if status != "OK":
        return []
    since = (datetime.now() - timedelta(days=10)).strftime("%d-%b-%Y")
    status, data = conn.uid("search", None, "SINCE", since)
    uids = data[0].split() if status == "OK" and data and data[0] else []
    items: list[tuple[str, str, int, bytes]] = []
    for i in range(0, len(uids), 200):
        chunk = b",".join(uids[i:i + 200]).decode()
        status, rows = conn.uid("fetch", chunk, "(UID INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER])")
        for part in rows or []:
            if not isinstance(part, tuple):
                continue
            meta = part[0].decode(errors="replace")
            uid = re.search(r"UID (\d+)", meta)
            date = re.search(r'INTERNALDATE "([^"]+)"', meta)
            size = re.search(r"RFC822\.SIZE (\d+)", meta)
            if uid and date and size:
                d = datetime.strptime(date.group(1), "%d-%b-%Y %H:%M:%S %z")
                items.append((uid.group(1), d.strftime("%a, %d %b %Y %H:%M:%S %z"), int(size.group(1)), part[1]))
    return items


def _fetch_raw(conn, uid: str) -> bytes | None:
    status, rows = conn.uid("fetch", uid, "(BODY.PEEK[])")
    for part in rows or []:
        if isinstance(part, tuple):
            return part[1]
    return None


def _send(host: str, user: str, password: str, attachments: list[bytes]) -> None:
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = REPORT_TO
    msg["Subject"] = f"Spam report: {len(attachments)} message(s) missed by the filter ({user})"
    msg.set_content(
        "Automatyczne zgłoszenie: wiadomości w załącznikach zostały oznaczone jako spam przez "
        "użytkownika / klienta pocztowego, a nie zostały oznaczone przez filtr serwera "
        "(brak X-Spam-Flag: YES). Proszę o użycie ich do nauki filtra.\n"
    )
    for raw in attachments:
        msg.add_attachment(raw, maintype="message", subtype="rfc822", filename="spam.eml")
    with smtplib.SMTP(host, 587, timeout=60) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--apply", action="store_true", help="Actually send reports (default: dry run).")
    ap.add_argument("--state-path", default="data/spam-reported.json")
    ap.add_argument("--min-age-hours", type=float, default=24)
    ap.add_argument("--max-age-days", type=float, default=8)
    ap.add_argument("--max-per-mailbox", type=int, default=40)
    ap.add_argument("--max-bytes", type=int, default=1_000_000)
    ap.add_argument("--mailbox-id", action="append", default=[])
    args = ap.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()  # type: ignore[call-arg]
    state_path = Path(args.state_path)
    state = _load_state(state_path)
    now = datetime.now(timezone.utc)
    summary = []
    for mb in settings.load_mailboxes():
        if args.mailbox_id and mb.mailbox_id not in args.mailbox_id:
            continue
        row = {"mailbox_id": mb.mailbox_id, "candidates": 0, "reported": 0}
        reported = set(state.get(mb.mailbox_id, []))
        try:
            with IMAPClient(mb) as client:
                conn = client.connection
                items = _fetch_items(conn, mb.imap_spam_folder)
                row["in_window"] = len(items)
                chosen = select_candidates(
                    items, reported, now=now, min_age_hours=args.min_age_hours,
                    max_age_days=args.max_age_days, max_bytes=args.max_bytes, limit=args.max_per_mailbox,
                    trusted_domains=trusted_domains_from(settings.automated_sender_keep),
                )
                row["candidates"] = len(chosen)
                if args.apply and chosen:
                    for i in range(0, len(chosen), BATCH):
                        batch = chosen[i:i + BATCH]
                        raws = [r for r in (_fetch_raw(conn, uid) for uid, _ in batch) if r]
                        if not raws:
                            continue
                        _send(mb.imap_host, mb.imap_user, mb.imap_pass.get_secret_value(), raws)
                        reported.update(key for _, key in batch)
                        row["reported"] += len(raws)
                        state[mb.mailbox_id] = sorted(reported)
                        _save_state(state_path, state)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        summary.append(row)
    print(json.dumps({"apply": args.apply, "at": now.isoformat(), "mailboxes": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
