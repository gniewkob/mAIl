from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mail_ai_agent.cleanup_cli import main
from mail_ai_agent.state_manager import MOVE_CLEANUP_PENDING_ACTION, StateManager


class FakeCleanupIMAPClient:
    def __init__(self, mailbox) -> None:
        self.mailbox = mailbox
        self.deleted: list[tuple[str, str]] = []
        self.validated: list[tuple[str, tuple[str, ...], bool]] = []

    def __enter__(self) -> "FakeCleanupIMAPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def delete_message(self, folder: str, uid: str) -> None:
        self.deleted.append((folder, uid))

    def validate_routing_setup(self, *, source_folder: str, target_folders: list[str], dry_run: bool) -> None:
        self.validated.append((source_folder, tuple(target_folders), dry_run))


class FakeFailingDeleteIMAPClient(FakeCleanupIMAPClient):
    def delete_message(self, folder: str, uid: str) -> None:
        self.deleted.append((folder, uid))
        raise RuntimeError("delete failed")


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
    instances: list[FakeCleanupIMAPClient] = []

    class CapturingClient(FakeCleanupIMAPClient):
        def __init__(self, mailbox) -> None:
            super().__init__(mailbox)
            instances.append(self)

    monkeypatch.setattr("mail_ai_agent.cleanup_cli.IMAPClient", CapturingClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cleanup_cli",
            "--apply",
        ],
    )
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "user@example.com")
    monkeypatch.setenv("IMAP_PASS", "secret")
    monkeypatch.setenv("STATE_DB_PATH", str(state_db))

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert instances[0].deleted == [("INBOX.AI-Review", "42")]
    record = manager.get_by_message_id("user_example_com", "msg-1")
    assert record is not None
    assert record.action_taken == "cleanup_source"
    assert record.status.value == "processed"


def test_cleanup_cli_does_not_mark_done_when_delete_fails(
    monkeypatch, tmp_path: Path
) -> None:
    state_db = tmp_path / "state.sqlite"
    manager = seed_cleanup_pending_record(state_db)

    monkeypatch.setattr("mail_ai_agent.cleanup_cli.IMAPClient", FakeFailingDeleteIMAPClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cleanup_cli",
            "--apply",
        ],
    )
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "user@example.com")
    monkeypatch.setenv("IMAP_PASS", "secret")
    monkeypatch.setenv("STATE_DB_PATH", str(state_db))

    main()

    record = manager.get_by_message_id("user_example_com", "msg-1")
    assert record is not None
    assert record.action_taken == MOVE_CLEANUP_PENDING_ACTION
    assert record.status.value == "cleanup_pending"


def seed_three_cleanup_pending_records(state_db: Path) -> StateManager:
    manager = StateManager(state_db)
    for i in range(1, 4):
        acquired = manager.acquire_lease(
            mailbox_id="user_example_com",
            message_id=f"msg-{i}",
            fingerprint=f"fp-{i}",
            imap_uid=str(40 + i),
            sender="client@example.com",
            subject=f"Subject {i}",
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


def test_cleanup_continues_after_single_delete_failure(tmp_path: Path, monkeypatch) -> None:
    """If record 1 delete fails, records 2 and 3 must still be cleaned."""
    state_db = tmp_path / "state.sqlite"
    manager = seed_three_cleanup_pending_records(state_db)

    instances: list[FakeCleanupIMAPClient] = []

    class FakePartialFailIMAPClient(FakeCleanupIMAPClient):
        def __init__(self, mailbox) -> None:
            super().__init__(mailbox)
            instances.append(self)

        def delete_message(self, folder: str, uid: str) -> None:
            if uid == "41":  # first UID fails
                raise RuntimeError("simulated delete failure")
            super().delete_message(folder, uid)

    monkeypatch.setattr("mail_ai_agent.cleanup_cli.IMAPClient", FakePartialFailIMAPClient)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cleanup_cli", "--apply"],
    )
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "user@example.com")
    monkeypatch.setenv("IMAP_PASS", "secret")
    monkeypatch.setenv("STATE_DB_PATH", str(state_db))

    main()

    # Records 2 and 3 (UIDs 42, 43) should be processed
    record2 = manager.get_by_message_id("user_example_com", "msg-2")
    record3 = manager.get_by_message_id("user_example_com", "msg-3")
    assert record2 is not None and record2.status.value == "processed"
    assert record3 is not None and record3.status.value == "processed"

    # Record 1 (UID 41) should still be cleanup_pending
    record1 = manager.get_by_message_id("user_example_com", "msg-1")
    assert record1 is not None and record1.status.value == "cleanup_pending"
