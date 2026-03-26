from __future__ import annotations

import os

import pytest

from mail_ai_agent.config import Settings
from mail_ai_agent.imap_client import IMAPClient


pytestmark = pytest.mark.integration


def _imap_env_available() -> bool:
    required = [
        "LIVE_IMAP_HOST",
        "LIVE_IMAP_USER",
        "LIVE_IMAP_PASS",
        "LIVE_IMAP_SOURCE_FOLDER",
    ]
    return all(os.getenv(name) for name in required) and os.getenv("RUN_LIVE_IMAP_TESTS") == "1"


@pytest.mark.skipif(not _imap_env_available(), reason="Set RUN_LIVE_IMAP_TESTS=1 and LIVE_IMAP_* vars to run live IMAP tests")
def test_live_imap_can_fetch_candidates() -> None:
    settings = Settings(
        IMAP_HOST=os.environ["LIVE_IMAP_HOST"],
        IMAP_USER=os.environ["LIVE_IMAP_USER"],
        IMAP_PASS=os.environ["LIVE_IMAP_PASS"],
        IMAP_SOURCE_FOLDER=os.environ["LIVE_IMAP_SOURCE_FOLDER"],
        IMAP_PORT=int(os.getenv("LIVE_IMAP_PORT", "993")),
    )
    mailbox = settings.load_mailboxes()[0]

    with IMAPClient(mailbox) as imap:
        candidates = imap.fetch_candidates(mailbox.imap_source_folder)

    assert isinstance(candidates, list)
