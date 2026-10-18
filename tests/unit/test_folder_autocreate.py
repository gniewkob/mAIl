from __future__ import annotations

import pytest

from mail_ai_agent.imap_client import IMAPClient

from test_imap_client import FakeFlakyConnection, make_mailbox


class _Conn(FakeFlakyConnection):
    def __init__(self) -> None:
        super().__init__()
        self.subscribed: list[str] = []

    def create(self, folder: str):
        self.create_calls.append(folder)
        self.list_data = [*self.list_data, f'(\\HasNoChildren) "/" "{folder}"'.encode()]
        return ("OK", [b"created"])

    def subscribe(self, folder: str):
        self.subscribed.append(folder)
        return ("OK", [b"subscribed"])


def _client(monkeypatch, conn):
    monkeypatch.setattr("mail_ai_agent.imap_client.imaplib.IMAP4_SSL", lambda *_, **__: conn)
    monkeypatch.setattr("mail_ai_agent.imap_client.time.sleep", lambda _: None)
    return IMAPClient(make_mailbox())


def test_live_run_creates_and_subscribes_missing_target(monkeypatch) -> None:
    conn = _Conn()
    with _client(monkeypatch, conn) as client:
        client.validate_runtime_setup(
            source_folder="INBOX.AI-Review", target_folders=["INBOX.Questions", "INBOX.Zakupy"], dry_run=False
        )
    assert conn.create_calls == ["INBOX.Zakupy"]
    assert conn.subscribed == ["INBOX.Zakupy"]


def test_dry_run_never_creates_and_still_fails(monkeypatch) -> None:
    conn = _Conn()
    with _client(monkeypatch, conn) as client, pytest.raises(RuntimeError, match="INBOX.Zakupy"):
        client.validate_runtime_setup(
            source_folder="INBOX.AI-Review", target_folders=["INBOX.Zakupy"], dry_run=True
        )
    assert conn.create_calls == []


def test_existing_targets_are_not_recreated(monkeypatch) -> None:
    conn = _Conn()
    with _client(monkeypatch, conn) as client:
        client.validate_runtime_setup(
            source_folder="INBOX.AI-Review", target_folders=["INBOX.Questions"], dry_run=False
        )
    assert conn.create_calls == [] and conn.subscribed == []
