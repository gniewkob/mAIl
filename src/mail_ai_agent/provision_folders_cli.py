from __future__ import annotations

import argparse
import json
from typing import Any

from .config import Settings
from .imap_client import IMAPClient


def _load_settings(env_file: str | None) -> Settings:
    return Settings(_env_file=env_file) if env_file else Settings()  # type: ignore[call-arg]


def _desired_folders(mailbox) -> list[str]:
    folders = [mailbox.imap_spam_folder, mailbox.imap_newsletter_folder, mailbox.imap_offer_folder]
    unique: list[str] = []
    for folder in folders:
        if folder not in unique:
            unique.append(folder)
    return unique


def run_provision_folders(
    *,
    settings: Settings,
    mailbox_id: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    mailboxes = settings.load_mailboxes()
    if mailbox_id is not None:
        mailboxes = [mailbox for mailbox in mailboxes if mailbox.mailbox_id == mailbox_id]
    if not mailboxes:
        raise ValueError("No mailboxes selected for provisioning.")

    payload: dict[str, Any] = {
        "apply": apply,
        "selected": len(mailboxes),
        "created": 0,
        "existing": 0,
        "missing": 0,
        "failed": 0,
        "results": [],
    }

    for mailbox in mailboxes:
        mailbox_result: dict[str, Any] = {
            "mailbox_id": mailbox.mailbox_id,
            "mailbox_user": mailbox.imap_user,
            "status": "ok",
            "folders": [],
        }
        payload["results"].append(mailbox_result)

        try:
            with IMAPClient(mailbox) as imap:
                existing_folders = set(imap.list_folders())
                for folder in _desired_folders(mailbox):
                    folder_result = {
                        "folder": folder,
                        "status": "existing" if folder in existing_folders else ("created" if apply else "missing"),
                    }
                    mailbox_result["folders"].append(folder_result)
                    if folder in existing_folders:
                        payload["existing"] += 1
                        continue
                    if apply:
                        imap.create_folder(folder)
                        payload["created"] += 1
                    else:
                        payload["missing"] += 1
        except Exception as exc:
            mailbox_result["status"] = "failed"
            mailbox_result["error"] = str(exc)
            payload["failed"] += 1

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision Junk, Newsletter and Offer folders across configured mailboxes")
    parser.add_argument("--env-file", default=None, help="Optional env file path")
    parser.add_argument("--mailbox-id", default=None, help="Optional mailbox id filter")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create missing folders. Default is dry-run only.",
    )
    args = parser.parse_args()

    payload = run_provision_folders(
        settings=_load_settings(args.env_file),
        mailbox_id=args.mailbox_id,
        apply=args.apply,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
