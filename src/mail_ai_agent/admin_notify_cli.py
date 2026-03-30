from __future__ import annotations

import argparse
import json
import textwrap

from .config import Settings
from .imap_client import IMAPClient
from .smtp_notifier import send_admin_email

UIDPLUS_SUBJECT = "[mAIl] IMAP UIDPLUS not supported — admin consultation required"

UIDPLUS_BODY = textwrap.dedent("""\
    Hello,

    This is an automated notification from the mAIl AI email routing system.

    FINDING: The IMAP server does not advertise the UIDPLUS capability.

    WHAT IS UIDPLUS?
    UIDPLUS (RFC 4315) enables targeted per-message expunge: the server can
    delete exactly one message by UID without affecting other flagged messages.

    CURRENT WORKAROUND:
    Without UIDPLUS the system uses folder-level EXPUNGE with a safety check:
    it verifies no other messages are already flagged as \\Deleted before
    proceeding. This is safe as long as the source folder (INBOX.AI-Review)
    is exclusively managed by this worker.

    ACTION REQUIRED:
    Please enable UIDPLUS on the IMAP server. For Dovecot this is enabled by
    default — check /etc/dovecot/conf.d/20-imap.conf. For other servers,
    consult your hosting provider.

    IMAP server: {imap_host}
    IMAP user:   {imap_user}
    Worker ID:   {worker_id}

    Regards,
    mAIl automated monitoring
""")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send admin notification about IMAP UIDPLUS support status"
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print email to stdout instead of sending")
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    mailboxes = settings.load_mailboxes()

    results: list[dict] = []
    for mailbox in mailboxes:
        with IMAPClient(mailbox) as imap:
            uidplus = imap.supports_uidplus()

        if uidplus:
            results.append({"mailbox_id": mailbox.mailbox_id, "uidplus": True, "action": "none"})
            continue

        body = UIDPLUS_BODY.format(
            imap_host=mailbox.imap_host,
            imap_user=mailbox.imap_user,
            worker_id=settings.worker_id,
        )

        if args.dry_run:
            print(f"--- DRY RUN: would send to {settings.admin_notify_email} ---")
            print(f"Subject: {UIDPLUS_SUBJECT}\n")
            print(body)
            results.append({"mailbox_id": mailbox.mailbox_id, "uidplus": False, "action": "dry_run"})
        else:
            send_admin_email(settings, subject=UIDPLUS_SUBJECT, body=body)
            results.append({"mailbox_id": mailbox.mailbox_id, "uidplus": False, "action": "email_sent"})

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
