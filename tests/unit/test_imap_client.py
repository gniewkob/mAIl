from __future__ import annotations

import imaplib

from mail_ai_agent.config import MailboxConfig
from mail_ai_agent.imap_client import IMAPClient


class FakeFlakyConnection:
    def __init__(self, *, fail_copy_times: int = 0, fail_search_times: int = 0) -> None:
        self.fail_copy_times = fail_copy_times
        self.fail_search_times = fail_search_times
        self.copy_attempts = 0
        self.search_attempts = 0

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        return ("OK", [b"logged-in"])

    def logout(self) -> tuple[str, list[bytes]]:
        return ("BYE", [b"logout"])

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        return ("OK", [b"1"])

    def uid(self, command: str, *args) -> tuple[str, list]:
        if command == "copy":
            self.copy_attempts += 1
            if self.copy_attempts <= self.fail_copy_times:
                raise imaplib.IMAP4.abort("copy aborted")
            return ("OK", [b"copied"])
        if command == "search":
            self.search_attempts += 1
            if self.search_attempts <= self.fail_search_times:
                raise imaplib.IMAP4.abort("search aborted")
            return ("OK", [b"42"])
        if command == "fetch":
            return ("OK", [(b'1 (INTERNALDATE "26-Mar-2026 10:00:00 +0000")', b"Subject: Test\n\nBody")])
        if command == "store":
            return ("OK", [b"stored"])
        raise AssertionError(f"Unexpected command: {command}")

    def expunge(self) -> tuple[str, list[bytes]]:
        return ("OK", [b"expunged"])


def make_mailbox() -> MailboxConfig:
    return MailboxConfig.model_validate(
        {
            "mailbox_id": "test",
            "imap_host": "imap.example.com",
            "imap_user": "user@example.com",
            "imap_pass": "secret",
            "imap_max_retries": 3,
            "imap_retry_backoff_seconds": 0.0,
        }
    )


def test_copy_message_retries_after_abort(monkeypatch) -> None:
    mailbox = make_mailbox()
    connections = [FakeFlakyConnection(fail_copy_times=1), FakeFlakyConnection(fail_copy_times=0)]

    def fake_ssl(host: str, port: int):
        assert host == mailbox.imap_host
        assert port == mailbox.imap_port
        return connections.pop(0)

    monkeypatch.setattr("mail_ai_agent.imap_client.imaplib.IMAP4_SSL", fake_ssl)
    monkeypatch.setattr("mail_ai_agent.imap_client.time.sleep", lambda _: None)

    with IMAPClient(mailbox) as client:
        client.copy_message("INBOX.AI-Review", "42", "INBOX.Questions")


def test_fetch_candidates_retries_after_abort(monkeypatch) -> None:
    mailbox = make_mailbox()
    connections = [FakeFlakyConnection(fail_search_times=1), FakeFlakyConnection(fail_search_times=0)]

    monkeypatch.setattr("mail_ai_agent.imap_client.imaplib.IMAP4_SSL", lambda *_: connections.pop(0))
    monkeypatch.setattr("mail_ai_agent.imap_client.time.sleep", lambda _: None)

    with IMAPClient(mailbox) as client:
        candidates = client.fetch_candidates("INBOX.AI-Review")

    assert len(candidates) == 1
    assert candidates[0].uid == "42"
