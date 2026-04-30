from __future__ import annotations

import argparse
import json
import sys

from .config import Settings
from .imap_client import IMAPClient
from .state_manager import StateManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Controlled cleanup for source-folder messages after partial move or legacy copy-only routing")
    parser.add_argument("--env-file", default=None, help="Optional env file path")
    parser.add_argument("--mailbox-id", default=None, help="Optional mailbox id when using multi-mailbox config")
    parser.add_argument("--apply", action="store_true", help="Actually mark source messages as deleted")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit of candidates to process")
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()  # type: ignore[call-arg]
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
        "count": len(candidates),
        "uids": [record.imap_uid for record in candidates if record.imap_uid],
    }

    if not args.apply:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    failed_uids: list[str] = []
    with IMAPClient(mailbox) as imap:
        imap.validate_runtime_setup(
            source_folder=mailbox.imap_source_folder,
            target_folders=[],
            dry_run=False,
        )
        current_uidvalidity = imap.get_uidvalidity(mailbox.imap_source_folder)
        cleaned_record_ids: list[int] = []
        for record in candidates:
            if not record.imap_uid:
                continue
            if record.uidvalidity and current_uidvalidity and record.uidvalidity != current_uidvalidity:
                print(
                    f"[WARN] Skipping UID {record.imap_uid}: UIDVALIDITY mismatch "
                    f"(stored={record.uidvalidity}, current={current_uidvalidity})",
                    file=sys.stderr,
                )
                continue
            try:
                imap.delete_message(mailbox.imap_source_folder, record.imap_uid)
                cleaned_record_ids.append(record.id)
            except Exception as exc:
                failed_uids.append(record.imap_uid)
                print(f"[WARN] Failed to clean UID {record.imap_uid}: {exc}", file=sys.stderr)
        for record_id in cleaned_record_ids:
            state.mark_cleanup_done(record_id)

    if failed_uids:
        payload["failed_uids"] = failed_uids
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
