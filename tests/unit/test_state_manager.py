from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from mail_ai_agent.schemas import WorkflowStatus
from mail_ai_agent.state_manager import StateManager


def test_acquire_lease_for_new_message(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")

    result = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate="26-Mar-2026 10:00:00 +0000",
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )

    assert result.outcome == "acquired"
    assert result.record is not None
    assert result.record.status == WorkflowStatus.PROCESSING
    assert result.record.attempt_count == 1


def test_active_lease_blocks_second_worker(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=600,
        max_retries=3,
    )

    result = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-2",
        lease_seconds=600,
        max_retries=3,
    )

    assert result.outcome == "locked"


def test_expired_lease_can_be_reacquired(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    initial = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=-1,
        max_retries=3,
    )
    assert initial.outcome == "acquired"

    result = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-2",
        lease_seconds=60,
        max_retries=3,
    )

    assert result.outcome == "acquired"
    assert result.record is not None
    assert result.record.attempt_count == 2


def test_processed_message_is_not_reacquired(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    initial = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert initial.record is not None
    manager.mark_processed(
        initial.record.id,
        category="question",
        confidence=0.8,
        target_folder="INBOX.Questions",
        action_taken="route_from_llm",
    )

    result = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-2",
        lease_seconds=60,
        max_retries=3,
    )

    assert result.outcome == "already_done"


def test_identity_conflict_is_reported(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    first = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert first.record is not None
    manager.mark_failed(first.record.id, error_message="test", error_type="TestError")

    second = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-2",
        imap_uid="11",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )

    assert second.outcome == "conflict"


def test_single_worker_lock_blocks_second_worker(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")

    first = manager.acquire_worker_lock(worker_id="worker-1", lease_seconds=60)
    second = manager.acquire_worker_lock(worker_id="worker-2", lease_seconds=60)

    assert first.acquired is True
    assert second.acquired is False
    assert second.lock_owner == "worker-1"


def test_worker_lock_can_be_released(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")

    manager.acquire_worker_lock(worker_id="worker-1", lease_seconds=60)
    manager.release_worker_lock(worker_id="worker-1")
    second = manager.acquire_worker_lock(worker_id="worker-2", lease_seconds=60)

    assert second.acquired is True


def test_same_identity_can_exist_in_different_mailboxes(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")

    first = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    second = manager.acquire_lease(
        mailbox_id="inbox_b",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )

    assert first.outcome == "acquired"
    assert second.outcome == "acquired"
    assert second.record is not None
    assert second.record.mailbox_id == "inbox_b"


def test_cleanup_pending_message_is_not_reacquired(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    initial = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-cleanup",
        fingerprint="fp-cleanup",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert initial.record is not None
    manager.mark_move_cleanup_pending(
        initial.record.id,
        category="question",
        confidence=0.9,
        target_folder="INBOX.Questions",
        error_message="delete failed",
        error_type="RuntimeError",
    )

    result = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-cleanup",
        fingerprint="fp-cleanup",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-2",
        lease_seconds=60,
        max_retries=3,
    )

    assert result.outcome == "already_done"
    assert result.record is not None
    assert result.record.status == WorkflowStatus.CLEANUP_PENDING


def test_content_fingerprint_fallback_deduplicates_messages_without_message_id(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    first = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id=None,
        fingerprint="identity-fp-1",
        content_fingerprint="content-fp-1",
        imap_uid="10",
        uidvalidity="999",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert first.record is not None
    manager.mark_processed(
        first.record.id,
        category="question",
        confidence=0.9,
        target_folder="INBOX.Questions",
        action_taken="move_route_from_llm",
    )

    second = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id=None,
        fingerprint="identity-fp-2",
        content_fingerprint="content-fp-1",
        imap_uid="11",
        uidvalidity="999",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-2",
        lease_seconds=60,
        max_retries=3,
    )

    assert second.outcome == "already_done"
    assert second.record is not None
    assert second.record.content_fingerprint == "content-fp-1"


def test_uidvalidity_change_is_reported_as_conflict(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    first = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-uidv",
        fingerprint="fp-uidv",
        imap_uid="10",
        uidvalidity="999",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert first.record is not None
    manager.mark_failed(first.record.id, error_message="test", error_type="TestError")

    second = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-uidv",
        fingerprint="fp-uidv",
        imap_uid="10",
        uidvalidity="1000",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-2",
        lease_seconds=60,
        max_retries=3,
    )

    assert second.outcome == "conflict"
    assert second.reason == "uidvalidity changed"


def test_state_manager_uses_wal_mode(tmp_path):
    import sqlite3
    from mail_ai_agent.state_manager import StateManager
    StateManager(tmp_path / "state.sqlite")
    with sqlite3.connect(tmp_path / "state.sqlite") as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal", f"Expected WAL mode, got: {mode}"


def test_state_manager_has_busy_timeout(tmp_path):
    from mail_ai_agent.state_manager import StateManager
    sm = StateManager(tmp_path / "state.sqlite")
    with sm._connect() as conn:
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout >= 5000, f"Expected busy_timeout >= 5000ms, got: {timeout}"


def test_worker_lock_acquired_once_by_first_caller(tmp_path):
    from mail_ai_agent.state_manager import StateManager
    sm = StateManager(tmp_path / "state.sqlite")
    r1 = sm.acquire_worker_lock(worker_id="w1", lease_seconds=60)
    r2 = sm.acquire_worker_lock(worker_id="w2", lease_seconds=60)
    assert r1.acquired is True
    assert r2.acquired is False
    assert r2.lock_owner == "w1"


def test_worker_lock_same_worker_can_refresh(tmp_path):
    from mail_ai_agent.state_manager import StateManager
    sm = StateManager(tmp_path / "state.sqlite")
    r1 = sm.acquire_worker_lock(worker_id="w1", lease_seconds=60)
    r2 = sm.acquire_worker_lock(worker_id="w1", lease_seconds=60)
    assert r1.acquired is True
    assert r2.acquired is True


def test_managed_connection_closes_after_release_worker_lock(tmp_path):
    manager = StateManager(tmp_path / "state.sqlite")

    class FakeConnection:
        def __init__(self) -> None:
            self.execute = Mock()
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self) -> None:
            self.closed = True

    fake_conn = FakeConnection()
    manager._connect = lambda: fake_conn  # type: ignore[method-assign]

    manager.release_worker_lock(worker_id="worker-1")

    fake_conn.execute.assert_called_once_with(
        "DELETE FROM worker_runtime_lock WHERE lock_name = ? AND lock_owner = ?",
        ("main", "worker-1"),
    )
    assert fake_conn.closed is True


def test_list_by_status_and_delete_record(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    acquired = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert acquired.record is not None
    manager.mark_uncertain(
        acquired.record.id,
        category="other",
        confidence=0.2,
        target_folder="INBOX.AI-Uncertain",
        target_uid="55",
        error_message="llm failed",
    )

    uncertain = manager.list_by_status(status=WorkflowStatus.UNCERTAIN)
    assert [record.id for record in uncertain] == [acquired.record.id]

    manager.delete_record(acquired.record.id)

    assert manager.get_by_id(acquired.record.id) is None
