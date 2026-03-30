from __future__ import annotations

import imaplib

from mail_ai_agent.config import MailboxConfig
from mail_ai_agent.imap_client import IMAPClient


class FakeFlakyConnection:
    def __init__(self, *, fail_copy_times: int = 0, fail_search_times: int = 0, search_uids: bytes = b"42") -> None:
        self.fail_copy_times = fail_copy_times
        self.fail_search_times = fail_search_times
        self.search_uids = search_uids
        self.copy_attempts = 0
        self.search_attempts = 0
        self.search_args: tuple | None = None
        self.fetch_args: list[tuple] = []

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        return ("OK", [b"logged-in"])

    def logout(self) -> tuple[str, list[bytes]]:
        return ("BYE", [b"logout"])

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        return ("OK", [b"1"])

    def response(self, code: str) -> tuple[str, list[bytes]] | None:
        if code == "UIDVALIDITY":
            return ("UIDVALIDITY", [b"999"])
        return None

    def uid(self, command: str, *args) -> tuple[str, list]:
        if command == "copy":
            self.copy_attempts += 1
            if self.copy_attempts <= self.fail_copy_times:
                raise imaplib.IMAP4.abort("copy aborted")
            return ("OK", [b"copied"])
        if command == "search":
            self.search_attempts += 1
            self.search_args = args
            if self.search_attempts <= self.fail_search_times:
                raise imaplib.IMAP4.abort("search aborted")
            return ("OK", [self.search_uids])
        if command == "fetch":
            self.fetch_args.append(args)
            # Build one response entry per UID in the batch set
            uid_set = args[0] if args else b""
            uids = uid_set.split(b",") if isinstance(uid_set, bytes) else uid_set.split(",")
            response = []
            for i, u in enumerate(uids, start=1):
                u_str = u.decode() if isinstance(u, bytes) else str(u)
                response.append(
                    (
                        f'{i} (UID {u_str} INTERNALDATE "26-Mar-2026 10:00:00 +0000" BODY[] {{17}}'.encode(),
                        b"Subject: Test\n\nBody",
                    )
                )
                response.append(b")")
            return ("OK", response)
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
            "imap_search_criterion": "ALL",
            "imap_fetch_limit": 100,
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
    assert candidates[0].uidvalidity == "999"


def test_fetch_candidates_uses_search_criterion_and_limit(monkeypatch) -> None:
    mailbox = make_mailbox().model_copy(update={"imap_search_criterion": "UNSEEN", "imap_fetch_limit": 2})
    connection = FakeFlakyConnection(search_uids=b"40 41 42")

    monkeypatch.setattr("mail_ai_agent.imap_client.imaplib.IMAP4_SSL", lambda *_: connection)
    monkeypatch.setattr("mail_ai_agent.imap_client.time.sleep", lambda _: None)

    with IMAPClient(mailbox) as client:
        candidates = client.fetch_candidates("INBOX.AI-Review")

    assert connection.search_args == (None, "UNSEEN")
    assert [candidate.uid for candidate in candidates] == ["41", "42"]
    assert all(candidate.uidvalidity == "999" for candidate in candidates)
    # Batch FETCH: exactly one fetch call with comma-separated UIDs
    assert len(connection.fetch_args) == 1
    assert connection.fetch_args[0][0] == "41,42"


def test_fetch_candidates_uses_single_batch_command():
    """N UIDs must produce exactly 1 FETCH command, not N."""
    from unittest.mock import MagicMock
    from pydantic import SecretStr
    from mail_ai_agent.imap_client import IMAPClient
    from mail_ai_agent.config import MailboxConfig
    import email as _email

    mailbox = MailboxConfig(
        mailbox_id="test", imap_host="imap.example.com",
        imap_user="u@example.com", imap_pass=SecretStr("secret"),
        imap_fetch_limit=3,
    )
    client = IMAPClient(mailbox)
    mock_conn = MagicMock()
    client.connection = mock_conn

    # Build a minimal raw email
    msg = _email.message.EmailMessage()
    msg["From"] = "a@b.com"
    msg["Subject"] = "Test"
    msg["Message-ID"] = "<t@t.com>"
    msg.set_content("hello")
    raw = msg.as_bytes()

    mock_conn.select.return_value = ("OK", [b"3"])
    mock_conn.response.return_value = (None, [b"12345"])

    batch_response = [
        (b'1 (UID 1 INTERNALDATE "01-Jan-2024 00:00:00 +0000" BODY[] {' + str(len(raw)).encode() + b'}', raw),
        b')',
        (b'2 (UID 2 INTERNALDATE "01-Jan-2024 00:00:00 +0000" BODY[] {' + str(len(raw)).encode() + b'}', raw),
        b')',
    ]
    mock_conn.uid.side_effect = [
        ("OK", [b"1 2"]),          # SEARCH
        ("OK", batch_response),    # single batch FETCH
    ]

    results = client.fetch_candidates("INBOX.Test")

    assert mock_conn.uid.call_count == 2, f"Expected 2 calls (search + batch fetch), got {mock_conn.uid.call_count}"
    fetch_call = mock_conn.uid.call_args_list[1]
    uid_arg = fetch_call[0][1]
    assert "," in str(uid_arg), f"Expected comma-separated UIDs, got: {uid_arg}"
    assert len(results) == 2


def test_get_uidvalidity_reads_folder_metadata(monkeypatch) -> None:
    mailbox = make_mailbox()
    connection = FakeFlakyConnection()

    monkeypatch.setattr("mail_ai_agent.imap_client.imaplib.IMAP4_SSL", lambda *_: connection)
    monkeypatch.setattr("mail_ai_agent.imap_client.time.sleep", lambda _: None)

    with IMAPClient(mailbox) as client:
        uidvalidity = client.get_uidvalidity("INBOX.AI-Review")

    assert uidvalidity == "999"
