from __future__ import annotations

import argparse
import json

from .config import Settings
from .imap_client import IMAPClient
from .state_manager import StateManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Controlled cleanup for source-folder messages after copy-only routing")
    parser.add_argument("--env-file", default=None, help="Optional env file path")
    parser.add_argument("--mailbox-id", default=None, help="Optional mailbox id when using multi-mailbox config")
    parser.add_argument("--apply", action="store_true", help="Actually mark source messages as deleted")
    parser.add_argument("--expunge", action="store_true", help="Expunge the source folder after deletion")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit of candidates to process")
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    state = StateManager(settings.state_db_path)
    mailboxes = settings.load_mailboxes()
    if args.mailbox_id:
        mailboxes = [mailbox for mailbox in mailboxes if mailbox.mailbox_id == args.mailbox_id]
    if not mailboxes:
        raise SystemExit("No mailboxes selected for cleanup.")
    if len(mailboxes) > 1:
        raise SystemExit("Cleanup requires exactly one mailbox. Use --mailbox-id.")
    mailbox = mailboxes[0]
    candidates = state.list_cleanup_candidates(mailbox_id=mailbox.mailbox_id, source_folder=mailbox.imap_source_folder)
    if args.limit is not None:
        candidates = candidates[: args.limit]

    payload = {
        "mailbox_id": mailbox.mailbox_id,
        "mailbox_user": mailbox.imap_user,
        "source_folder": mailbox.imap_source_folder,
        "apply": args.apply,
        "expunge": args.expunge,
        "count": len(candidates),
        "uids": [record.imap_uid for record in candidates if record.imap_uid],
    }

    if not args.apply:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    with IMAPClient(mailbox) as imap:
        for record in candidates:
            if not record.imap_uid:
                continue
            imap.mark_deleted(mailbox.imap_source_folder, record.imap_uid)
            state.mark_cleanup_done(record.id)
        if args.expunge and candidates:
            imap.expunge(mailbox.imap_source_folder)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
