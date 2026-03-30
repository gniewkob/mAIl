from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    assert mailboxes[0].imap_max_retries == 3
    assert mailboxes[0].imap_search_criterion == "ALL"
    assert mailboxes[0].imap_fetch_limit == 100
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
    assert mailboxes[1].imap_max_retries == 3
    assert mailboxes[1].imap_search_criterion == "ALL"
    assert mailboxes[1].imap_fetch_limit == 100
    assert "secret-a" not in repr(mailboxes[0])
    assert "secret-b" not in repr(mailboxes[1])


@pytest.mark.parametrize(
    "criterion",
    ["ALL", "UNSEEN", "UNANSWERED", "FLAGGED", "UNSEEN UNANSWERED", "UNSEEN FLAGGED"],
)
def test_supported_imap_search_criteria_are_accepted(criterion: str) -> None:
    settings = Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
        IMAP_SEARCH_CRITERION=criterion,
    )

    mailbox = settings.load_mailboxes()[0]
    assert mailbox.imap_search_criterion == criterion


def test_unsupported_imap_search_criterion_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported IMAP_SEARCH_CRITERION"):
        Settings(
            IMAP_HOST="imap.example.com",
            IMAP_USER="user@example.com",
            IMAP_PASS="secret",
            IMAP_SEARCH_CRITERION='TEXT "hello world"',
        )


def test_normalize_mailbox_respects_zero_fetch_limit(tmp_path: Path) -> None:
    import json
    from mail_ai_agent.config import Settings
    manifest = tmp_path / "mailboxes.json"
    manifest.write_text(json.dumps([{
        "imap_user": "u@example.com", "imap_pass": "secret",
        "imap_host": "imap.example.com", "imap_fetch_limit": 0,
    }]), encoding="utf-8")
    settings = Settings(IMAP_HOST="fallback.example.com", MAILBOXES_CONFIG_PATH=str(manifest))
    mailboxes = settings.load_mailboxes()
    assert mailboxes[0].imap_fetch_limit == 0


def test_audit_less_restrictive_than_state_raises():
    import pytest
    from pydantic import ValidationError

    with pytest.raises((ValueError, ValidationError)):
        from mail_ai_agent.config import Settings

        Settings(
            IMAP_HOST="h",
            IMAP_USER="u",
            IMAP_PASS="p",
            AUDIT_REDACT_PII=False,
            STATE_REDACT_PII=True,
        )
