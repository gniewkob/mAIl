from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .imap_client import IMAPClient


def main() -> None:
    parser = argparse.ArgumentParser(description="IMAP preflight validation for AI Mail Triage")
    parser.add_argument("--env-file", default=None, help="Optional env file path")
    parser.add_argument("--mailbox-id", default=None, help="Optional mailbox id filter")
    args = parser.parse_args()

    # Check .env file permissions if provided
    if args.env_file:
        env_path = Path(args.env_file)
        if env_path.exists():
            env_stat_mode = oct(env_path.stat().st_mode)[-3:]
            if env_stat_mode != "600":
                print(f"[WARN] {env_path} permissions are {env_stat_mode} — should be 600 (run: chmod 600 {env_path})")
            else:
                print(f"[OK]   {env_path} permissions: {env_stat_mode}")

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    mailboxes = settings.load_mailboxes()
    if args.mailbox_id:
        mailboxes = [mailbox for mailbox in mailboxes if mailbox.mailbox_id == args.mailbox_id]
    if not mailboxes:
        raise SystemExit("No mailboxes selected for preflight.")

    results: list[dict[str, object]] = []
    all_ok = True
    for mailbox in mailboxes:
        payload = {
            "mailbox_id": mailbox.mailbox_id,
            "mailbox_user": mailbox.imap_user,
            "source_folder": mailbox.imap_source_folder,
            "dry_run": settings.dry_run,
            "ok": True,
        }
        try:
            with IMAPClient(mailbox) as imap:
                imap.validate_routing_setup(
                    source_folder=mailbox.imap_source_folder,
                    target_folders=[
                        mailbox.imap_uncertain_folder,
                        mailbox.imap_appointments_folder,
                        mailbox.imap_questions_folder,
                        mailbox.imap_complaints_folder,
                        mailbox.imap_other_folder,
                        mailbox.imap_billing_folder,
                        mailbox.imap_system_folder,
                    ],
                    dry_run=settings.dry_run,
                )
        except Exception as exc:  # pragma: no cover - exercised via CLI contract tests
            payload["ok"] = False
            payload["error"] = str(exc)
            all_ok = False
        results.append(payload)

    print(json.dumps({"ok": all_ok, "results": results}, ensure_ascii=False, indent=2))
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
