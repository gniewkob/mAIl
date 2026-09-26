"""Index of what the owner has sent: Message-IDs and recipient addresses (read-only IMAP).

Strongest non-LLM signal for "this mail is for me": a reply to a message I sent
(In-Reply-To / References), or mail from someone I have written to. Values are stored as
sha256 hashes in data/sent-index.json (0600), never as plain addresses.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python -m mail_ai_agent.sent_index [--days 1095]
"""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import getaddresses, parseaddr
from pathlib import Path

DEFAULT_PATH = Path("data/sent-index.json")
_SENT_NAME = re.compile(r"(^|[./])(sent|sent items|sent messages|sent-mail|wys&aui-ane|wyslane)$", re.IGNORECASE)
_MSGID = re.compile(r"<[^<>\s]+>")


def _h(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:24]


def message_ids_in(value: str | None) -> list[str]:
    return _MSGID.findall(value or "")


@dataclass
class SentIndex:
    message_ids: set[str] = field(default_factory=set)
    correspondents: set[str] = field(default_factory=set)
    own_addresses: set[str] = field(default_factory=set)
    built_at: str = ""

    def is_reply_to_me(self, in_reply_to: str | None, references: str | None) -> bool:
        ids = message_ids_in(in_reply_to) + message_ids_in(references)
        return any(_h(i) in self.message_ids for i in ids)

    def is_correspondent(self, sender: str | None) -> bool:
        address = parseaddr(sender or "")[1].lower()
        if not address or _h(address) in self.own_addresses:
            return False
        return _h(address) in self.correspondents

    def save(self, path: Path = DEFAULT_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"built_at": self.built_at, "message_ids": sorted(self.message_ids),
                   "correspondents": sorted(self.correspondents), "own_addresses": sorted(self.own_addresses)}
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "SentIndex":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(set(data.get("message_ids", [])), set(data.get("correspondents", [])),
                   set(data.get("own_addresses", [])), str(data.get("built_at", "")))


def sent_folders(conn) -> list[str]:
    from .imap_client import _parse_list_response

    status, data = conn.list()
    out = []
    for item in (data or []) if status == "OK" else []:
        line = item.decode(errors="replace") if isinstance(item, bytes) else str(item)
        name = _parse_list_response(item)
        if name and ("\\sent" in line.lower() or _SENT_NAME.search(name)):
            out.append(name)
    return list(dict.fromkeys(out))


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def collect_from_folder(conn, folder: str, since: str, index: SentIndex, batch: int = 300) -> int:
    if conn.select(_quote(folder), readonly=True)[0] != "OK":
        return 0
    status, data = conn.uid("search", None, "SINCE", since)
    uids = data[0].split() if status == "OK" and data and data[0] else []
    for i in range(0, len(uids), batch):
        chunk = b",".join(uids[i:i + batch]).decode()
        status, rows = conn.uid("fetch", chunk, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID TO CC BCC)])")
        for part in rows or []:
            if not isinstance(part, tuple):
                continue
            msg = email.message_from_bytes(part[1])
            for mid in message_ids_in(msg.get("Message-ID")):
                index.message_ids.add(_h(mid))
            for _, addr in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", [])):
                if "@" in addr:
                    index.correspondents.add(_h(addr))
    return len(uids)


def build(settings, days: int = 1095) -> tuple[SentIndex, dict[str, int]]:
    from .imap_client import IMAPClient

    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    index = SentIndex(built_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    stats: dict[str, int] = {}
    for mb in settings.load_mailboxes():
        index.own_addresses.add(_h(mb.imap_user))
        try:
            with IMAPClient(mb) as client:
                conn = client.connection
                for folder in sent_folders(conn):
                    stats[f"{mb.mailbox_id}:{folder}"] = collect_from_folder(conn, folder, since, index)
        except Exception as exc:  # one broken account must not block the others
            stats[f"{mb.mailbox_id}:error"] = -1
            print(f"{mb.mailbox_id}: {type(exc).__name__}: {exc}")
    index.correspondents -= index.own_addresses
    return index, stats


def main() -> None:
    from .config import Settings

    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--out", default=str(DEFAULT_PATH))
    args = ap.parse_args()
    settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    index, stats = build(settings, args.days)
    index.save(Path(args.out))
    print(json.dumps({"sent_messages": sum(v for v in stats.values() if v > 0), "message_ids": len(index.message_ids),
                      "correspondents": len(index.correspondents), "folders": stats}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
