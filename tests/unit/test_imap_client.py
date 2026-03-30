from __future__ import annotations

import imaplib

from mail_ai_agent.config import MailboxConfig
from mail_ai_agent.imap_client import IMAPClient


class FakeFlakyConnection:
    def __init__(
        self,
        *,
        fail_copy_times: int = 0,
        fail_search_times: int = 0,
        search_uids: bytes = b"42",
        deleted_search_uids: bytes = b"",
    ) -> None:
        self.fail_copy_times = fail_copy_times
        self.fail_search_times = fail_search_times
        self.search_uids = search_uids
        self.deleted_search_uids = deleted_search_uids
        self.copy_attempts = 0
        self.search_attempts = 0
        self.search_args: tuple | None = None
        self.fetch_args: list[tuple] = []
        self.store_calls: list[tuple] = []
        self.expunge_calls = 0
        self.capabilities = {b"IMAP4REV1", b"UIDPLUS"}
        self.responses: dict[str, tuple[str, list[bytes]]] = {
            "UIDVALIDITY": ("UIDVALIDITY", [b"999"]),
            "COPYUID": ("COPYUID", [b"999 42 142"]),
        }

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        return ("OK", [b"logged-in"])

    def logout(self) -> tuple[str, list[bytes]]:
        return ("BYE", [b"logout"])

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        return ("OK", [b"1"])

    def response(self, code: str) -> tuple[str, list[bytes]] | None:
        return self.responses.get(code)

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
            if args == (None, "DELETED"):
                return ("OK", [self.deleted_search_uids])
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
            self.store_calls.append(args)
            return ("OK", [b"stored"])
        if command == "expunge":
            return ("OK", [b"expunged"])
        raise AssertionError(f"Unexpected command: {command}")

    def expunge(self) -> tuple[str, list[bytes]]:
        self.expunge_calls += 1
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


def test_validate_routing_setup_requires_uidplus_or_explicit_override(monkeypatch) -> None:
    mailbox = make_mailbox()
    connection = FakeFlakyConnection()
    connection.capabilities = {b"IMAP4REV1"}

    monkeypatch.setattr("mail_ai_agent.imap_client.imaplib.IMAP4_SSL", lambda *_: connection)
    monkeypatch.setattr("mail_ai_agent.imap_client.time.sleep", lambda _: None)

    with IMAPClient(mailbox) as client:
        try:
            client.validate_routing_setup(
                source_folder="INBOX.AI-Review",
                target_folders=["INBOX.Questions"],
                dry_run=False,
            )
        except RuntimeError as exc:
            assert "UIDPLUS" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError")


def test_delete_message_uses_uid_expunge_when_supported(monkeypatch) -> None:
    mailbox = make_mailbox()
    connection = FakeFlakyConnection()

    monkeypatch.setattr("mail_ai_agent.imap_client.imaplib.IMAP4_SSL", lambda *_: connection)
    monkeypatch.setattr("mail_ai_agent.imap_client.time.sleep", lambda _: None)

    with IMAPClient(mailbox) as client:
        client.delete_message("INBOX.AI-Review", "42")


def test_delete_message_refuses_folder_expunge_when_other_deleted_messages_exist(monkeypatch) -> None:
    mailbox = make_mailbox().model_copy(update={"imap_allow_folder_expunge": True})
    connection = FakeFlakyConnection(deleted_search_uids=b"41")
    connection.capabilities = {b"IMAP4REV1"}

    monkeypatch.setattr("mail_ai_agent.imap_client.imaplib.IMAP4_SSL", lambda *_: connection)
    monkeypatch.setattr("mail_ai_agent.imap_client.time.sleep", lambda _: None)

    with IMAPClient(mailbox) as client:
        try:
            client.delete_message("INBOX.AI-Review", "42")
        except RuntimeError as exc:
            assert "other deleted messages" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError")

    assert connection.store_calls == []
    assert connection.expunge_calls == 0


def test_delete_message_rolls_back_deleted_flag_when_deleted_set_changes(monkeypatch) -> None:
    mailbox = make_mailbox().model_copy(update={"imap_allow_folder_expunge": True})
    connection = FakeFlakyConnection(deleted_search_uids=b"42 41")
    connection.capabilities = {b"IMAP4REV1"}

    monkeypatch.setattr("mail_ai_agent.imap_client.imaplib.IMAP4_SSL", lambda *_: connection)
    monkeypatch.setattr("mail_ai_agent.imap_client.time.sleep", lambda _: None)

    with IMAPClient(mailbox) as client:
        try:
            client.delete_message("INBOX.AI-Review", "42")
        except RuntimeError as exc:
            assert "deleted set is" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError")

    assert connection.store_calls == [
        ("42", "+FLAGS.SILENT", "(\\Deleted)"),
        ("42", "-FLAGS.SILENT", "(\\Deleted)"),
    ]
    assert connection.expunge_calls == 0


def test_fetch_candidates_filters_non_numeric_uids():
    """fetch_candidates must drop UIDs that are not purely numeric."""
    from unittest.mock import MagicMock
    from pydantic import SecretStr
    from mail_ai_agent.config import MailboxConfig
    from mail_ai_agent.imap_client import IMAPClient

    mailbox = MailboxConfig(
        mailbox_id="test",
        imap_host="h",
        imap_user="u@h",
        imap_pass=SecretStr("p"),
    )
    client = IMAPClient(mailbox)

    mock_conn = MagicMock()
    client.connection = mock_conn

    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.response.return_value = ("OK", [b"12345"])

    def fake_run_with_retry(op_name, func):
        return func()

    client._run_with_retry = fake_run_with_retry

    mock_conn.uid.side_effect = [
        ("OK", [b"42 99 INJECT"]),  # SEARCH: mix of valid + invalid
        ("OK", []),                  # FETCH result (empty, doesn't matter)
    ]

    messages = client.fetch_candidates("INBOX")
    fetch_call = mock_conn.uid.call_args_list[1]
    uid_arg = fetch_call[0][1]  # second positional arg to uid("fetch", uid_set, ...)
    assert "INJECT" not in uid_arg, f"Non-numeric UID 'INJECT' must be filtered, got: {uid_arg}"
    assert "42" in uid_arg
    assert "99" in uid_arg


def test_copy_message_returns_target_uid_from_copyuid(monkeypatch) -> None:
    mailbox = make_mailbox()
    connection = FakeFlakyConnection()

    monkeypatch.setattr("mail_ai_agent.imap_client.imaplib.IMAP4_SSL", lambda *_: connection)
    monkeypatch.setattr("mail_ai_agent.imap_client.time.sleep", lambda _: None)

    with IMAPClient(mailbox) as client:
        target_uid = client.copy_message("INBOX.AI-Review", "42", "INBOX.Questions")

    assert target_uid == "142"
