from __future__ import annotations

import argparse
import json
import hashlib
import sqlite3
import unicodedata
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from .config import Settings
from .imap_client import IMAPClient
from .quality_feedback_cli import append_feedback

_ALLOWED = {
    "appointment",
    "question",
    "complaint",
    "spam",
    "newsletter",
    "offer",
    "billing",
    "system",
    "other",
}


def _load_candidates(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    rows = payload.get("candidates", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _terminal_text(value: str, *, limit: int) -> str:
    return "".join(
        char for char in value if char in "\n\t" or not unicodedata.category(char).startswith("C")
    )[:limit]


def _message_preview(raw: bytes, expected_hash: str) -> dict[str, str]:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    message_id = str(message.get("Message-ID") or "").strip()
    if not message_id or hashlib.sha256(message_id.encode("utf-8")).hexdigest() != expected_hash:
        raise ValueError("Message identity mismatch")
    body = message.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None and body.get_content_disposition() != "attachment":
        try:
            text = body.get_content()
        except (LookupError, UnicodeError):
            text = (body.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        if body.get_content_type() == "text/html":
            parser = _HTMLText()
            parser.feed(text)
            text = "".join(parser.parts)
    return {
        "sender": _terminal_text(str(message.get("From") or ""), limit=300),
        "subject": _terminal_text(str(message.get("Subject") or ""), limit=500),
        "body": _terminal_text(text.strip(), limit=3000) or "[No readable text in preview]",
    }


def _state_locator(row: dict[str, object], state_db: Path) -> dict[str, object]:
    if not state_db.exists():
        return {}
    mailbox_id = str(row.get("mailbox_id") or "")
    digest = str(row.get("message_id_sha256") or "")
    with sqlite3.connect(state_db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT mailbox_id, message_id, source_folder, target_folder FROM email_processing_state WHERE mailbox_id = ?"
        params: tuple = (mailbox_id,)
        if row.get("state_record_id") is not None:
            query += " AND id = ?"
            params += (row["state_record_id"],)
        for record in conn.execute(query, params):
            message_id = str(record["message_id"] or "").strip()
            if message_id and hashlib.sha256(message_id.encode("utf-8")).hexdigest() == digest:
                return dict(record)
    return {}


def _load_preview(row: dict[str, object], settings: Settings, state_db: Path) -> dict[str, str]:
    mailbox_id = str(row.get("mailbox_id") or "")
    mailbox = next((item for item in settings.load_mailboxes() if item.mailbox_id == mailbox_id), None)
    if mailbox is None:
        raise ValueError("Unknown review mailbox")
    locator = _state_locator(row, state_db)
    digest = str(row.get("message_id_sha256") or "")
    with IMAPClient(mailbox) as client:
        folder = str(row.get("source_folder") or "")
        uid = str(row.get("imap_uid") or "")
        if folder and uid:
            try:
                return _message_preview(client.fetch_message_preview_bytes(folder, uid), digest)
            except (RuntimeError, ValueError):
                pass
        message_id = str(locator.get("message_id") or "")
        if not message_id:
            raise ValueError("No verified message locator")
        folders = list(dict.fromkeys(str(item) for item in [
            locator.get("target_folder"), locator.get("source_folder"),
            mailbox.imap_uncertain_folder, mailbox.imap_source_folder, "INBOX",
            *client.list_folders(),
        ] if item))
        for folder in folders:
            try:
                for uid in client.find_uids_by_message_id(folder, message_id):
                    try:
                        return _message_preview(client.fetch_message_preview_bytes(folder, uid), digest)
                    except ValueError:
                        continue
            except RuntimeError:
                continue
    raise ValueError("Message not found in mailbox")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive feedback review session (y/n/s/q).")
    parser.add_argument("--candidates", default="logs/feedback-candidates-daily.json")
    parser.add_argument("--feedback-log", default="logs/quality-feedback.jsonl")
    parser.add_argument("--mailbox-id", default=None, help="Filter candidates to one mailbox")
    parser.add_argument("--env-file", default=".env.multi.prod", help="Local IMAP configuration for read-only preview")
    parser.add_argument("--state-db", default=None, help="State DB used to locate moved messages")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    candidates = _load_candidates(Path(args.candidates))
    if args.mailbox_id:
        candidates = [row for row in candidates if row.get("mailbox_id") == args.mailbox_id]
    candidates = candidates[: max(1, args.limit)]
    if not candidates:
        print("No candidates found.")
        return

    try:
        settings = Settings(_env_file=args.env_file)
    except Exception:
        raise SystemExit("Cannot load local preview configuration; check --env-file.") from None
    state_db = Path(args.state_db) if args.state_db else settings.state_db_path

    appended = 0
    skipped = 0
    print(f"Loaded {len(candidates)} candidates. Commands: y=accept, n=correct, s=skip, q=quit")

    for i, row in enumerate(candidates, start=1):
        predicted = str(row.get("predicted_category") or "other").strip().lower()
        msg_hash = str(row.get("message_id_sha256") or "")
        ts = str(row.get("timestamp") or "")
        reason = str(row.get("review_reason") or "manual_review")
        conf = row.get("confidence", "")
        source_folder = str(row.get("source_folder") or "")
        imap_uid = str(row.get("imap_uid") or "")
        state_status = str(row.get("state_status") or "")

        print("\n" + "-" * 72)
        print(f"[{i}/{len(candidates)}] predicted={predicted} confidence={conf} reason={reason}")
        if source_folder:
            print(f"source_folder={source_folder} imap_uid={imap_uid}")
        if state_status:
            print(f"state_status={state_status}")
        try:
            preview = _load_preview(row, settings, state_db)
        except Exception:
            print("Preview unavailable. This candidate will be skipped; no feedback saved.")
            skipped += 1
            continue
        print(f"From: {preview['sender']}")
        print(f"Subject: {preview['subject']}")
        print(f"\n{preview['body']}\n")

        cmd = input("Decision [y/n/s/q]: ").strip().lower()
        if cmd == "q":
            break
        if cmd == "s" or cmd == "":
            skipped += 1
            continue

        corrected = predicted
        outcome_ok = True
        outcome_note = "manual_accept"
        note_reason = reason

        if cmd == "n":
            print("Categories: " + ", ".join(sorted(_ALLOWED)))
            raw = input("Corrected category: ").strip().lower()
            if raw not in _ALLOWED:
                print("Invalid category, skipping.")
                skipped += 1
                continue
            corrected = raw
            outcome_ok = False
            outcome_note = "manual_correction"
            note_reason = f"manual_correction:{reason}"
        elif cmd != "y":
            print("Unknown command, skipping.")
            skipped += 1
            continue

        append_feedback(
            feedback_log=Path(args.feedback_log),
            mailbox_id=str(row["mailbox_id"]),
            message_id=f"sha256:{msg_hash}" if msg_hash else f"session:{i}",
            predicted_category=predicted,
            corrected_category=corrected,
            reason=note_reason,
            message_id_sha256=msg_hash or None,
            prediction_timestamp=ts or None,
            outcome_ok=outcome_ok,
            outcome_note=outcome_note,
        )
        appended += 1

    print(f"\nDone. appended={appended} skipped={skipped}")


if __name__ == "__main__":
    main()
