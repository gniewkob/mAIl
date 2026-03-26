from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mail_ai_agent.cleanup_cli import main
from mail_ai_agent.state_manager import MOVE_CLEANUP_PENDING_ACTION, StateManager


class FakeCleanupIMAPClient:
    deleted: list[tuple[str, str]] = []
    expunged: list[str] = []

    def __init__(self, mailbox) -> None:
        self.mailbox = mailbox

    def __enter__(self) -> "FakeCleanupIMAPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def mark_deleted(self, folder: str, uid: str) -> None:
        self.deleted.append((folder, uid))

    def expunge(self, folder: str) -> None:
        self.expunged.append(folder)


class FakeFailingExpungeIMAPClient(FakeCleanupIMAPClient):
    def expunge(self, folder: str) -> None:
        self.expunged.append(folder)
        raise RuntimeError("expunge failed")


def seed_cleanup_pending_record(state_db: Path) -> StateManager:
    manager = StateManager(state_db)
    acquired = manager.acquire_lease(
        mailbox_id="user_example_com",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="42",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert acquired.record is not None
    manager.mark_move_cleanup_pending(
        acquired.record.id,
        category="question",
        confidence=0.9,
        target_folder="INBOX.Questions",
        error_message="delete failed",
        error_type="RuntimeError",
    )
    return manager


def test_cleanup_cli_apply_marks_cleanup_done_after_success(monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state_db = tmp_path / "state.sqlite"
    manager = seed_cleanup_pending_record(state_db)
    FakeCleanupIMAPClient.deleted = []
    FakeCleanupIMAPClient.expunged = []

    monkeypatch.setattr("mail_ai_agent.cleanup_cli.IMAPClient", FakeCleanupIMAPClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cleanup_cli",
            "--apply",
            "--expunge",
        ],
    )
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "user@example.com")
    monkeypatch.setenv("IMAP_PASS", "secret")
    monkeypatch.setenv("STATE_DB_PATH", str(state_db))

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert FakeCleanupIMAPClient.deleted == [("INBOX.AI-Review", "42")]
    assert FakeCleanupIMAPClient.expunged == ["INBOX.AI-Review"]
    record = manager.get_by_message_id("user_example_com", "msg-1")
    assert record is not None
    assert record.action_taken == "cleanup_source"
    assert record.status.value == "processed"


def test_cleanup_cli_does_not_mark_done_when_expunge_fails(
    monkeypatch, tmp_path: Path
) -> None:
    state_db = tmp_path / "state.sqlite"
    manager = seed_cleanup_pending_record(state_db)
    FakeFailingExpungeIMAPClient.deleted = []
    FakeFailingExpungeIMAPClient.expunged = []

    monkeypatch.setattr("mail_ai_agent.cleanup_cli.IMAPClient", FakeFailingExpungeIMAPClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cleanup_cli",
            "--apply",
            "--expunge",
        ],
    )
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "user@example.com")
    monkeypatch.setenv("IMAP_PASS", "secret")
    monkeypatch.setenv("STATE_DB_PATH", str(state_db))

    with pytest.raises(RuntimeError, match="expunge failed"):
        main()

    record = manager.get_by_message_id("user_example_com", "msg-1")
    assert record is not None
    assert record.action_taken == MOVE_CLEANUP_PENDING_ACTION
    assert record.status.value == "cleanup_pending"
