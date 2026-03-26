from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.config import Settings


def test_settings_fall_back_to_single_mailbox() -> None:
    settings = Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
    )

    mailboxes = settings.load_mailboxes()

    assert len(mailboxes) == 1
    assert mailboxes[0].mailbox_id == "user_example_com"
    assert mailboxes[0].imap_source_folder == "INBOX.AI-Review"
    assert "secret" not in repr(mailboxes[0])


def test_settings_load_mailboxes_from_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "mailboxes.json"
    manifest.write_text(
        json.dumps(
            {
                "mailboxes": [
                    {
                        "mailbox_id": "kontakt",
                        "imap_user": "kontakt@example.com",
                        "imap_pass": "secret-a",
                        "imap_source_folder": "INBOX.Test-AI-Review",
                    },
                    {
                        "imap_user": "shop@example.com",
                        "imap_pass": "secret-b",
                        "imap_other_folder": "INBOX.Custom-Other",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        IMAP_HOST="imap.example.com",
        MAILBOXES_CONFIG_PATH=manifest,
        IMAP_OTHER_FOLDER="INBOX.Other",
    )

    mailboxes = settings.load_mailboxes()

    assert [mailbox.mailbox_id for mailbox in mailboxes] == ["kontakt", "shop_example_com"]
    assert mailboxes[0].imap_source_folder == "INBOX.Test-AI-Review"
    assert mailboxes[1].imap_other_folder == "INBOX.Custom-Other"
    assert mailboxes[1].imap_host == "imap.example.com"
    assert "secret-a" not in repr(mailboxes[0])
    assert "secret-b" not in repr(mailboxes[1])
