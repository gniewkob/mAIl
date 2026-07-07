from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mail_ai_agent.constants import WorkflowStatus
from mail_ai_agent.repositories.sqlite_repositories import (
    SqliteCleanupRepository,
    SqliteLeaseRepository,
    SqliteStateRepository,
    SqliteWorkerLockRepository,
)
from mail_ai_agent.state_manager_v2 import StateManagerV2


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_state.sqlite"


@pytest.fixture
def state_manager(db_path: Path) -> StateManagerV2:
    return StateManagerV2(db_path)


@pytest.fixture
def cleanup_repo(db_path: Path, state_manager: StateManagerV2) -> SqliteCleanupRepository:
    return SqliteCleanupRepository(db_path)


@pytest.fixture
def lease_repo(db_path: Path, state_manager: StateManagerV2) -> SqliteLeaseRepository:
    return SqliteLeaseRepository(db_path)


@pytest.fixture
def state_repo(db_path: Path, state_manager: StateManagerV2) -> SqliteStateRepository:
    return SqliteStateRepository(db_path)


@pytest.fixture
def worker_lock_repo(db_path: Path, state_manager: StateManagerV2) -> SqliteWorkerLockRepository:
    return SqliteWorkerLockRepository(db_path)


# --- Cleanup Repository Tests ---

def test_acquire_cleanup_lock_success(cleanup_repo: SqliteCleanupRepository, state_manager: StateManagerV2):
    # Setup: Create a record in CLEANUP_PENDING state
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cleanup_repo.db_path) as conn:
        conn.execute(
            """INSERT INTO email_processing_state
               (mailbox_id, fingerprint, sender, subject, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("mailbox-1", "fp-1", "sender", "subject", WorkflowStatus.CLEANUP_PENDING.value, now, now)
        )
        record_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Test: Acquire lock
    acquired = cleanup_repo.acquire_cleanup_lock(record_id, "worker-1", 60)
    assert acquired is True

    # Verify: Check database state
    conn = sqlite3.connect(cleanup_repo.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM email_processing_state WHERE id = ?", (record_id,)).fetchone()
    record = cleanup_repo._row_to_record(row)
    assert record.lock_owner == "worker-1"
    assert record.lock_expires_at is not None


def test_acquire_cleanup_lock_already_locked(cleanup_repo: SqliteCleanupRepository):
    # Setup: Create a record already locked by another worker
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(seconds=60)).isoformat()

    with sqlite3.connect(cleanup_repo.db_path) as conn:
        conn.execute(
            """INSERT INTO email_processing_state
               (mailbox_id, fingerprint, status, lock_owner, lock_expires_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("mailbox-1", "fp-1", WorkflowStatus.CLEANUP_PENDING.value, "worker-other", expires_iso, now_iso, now_iso)
        )
        record_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Test: Try to acquire lock
    acquired = cleanup_repo.acquire_cleanup_lock(record_id, "worker-1", 60)
    assert acquired is False


def test_acquire_cleanup_lock_expired_lock(cleanup_repo: SqliteCleanupRepository):
    # Setup: Create a record with an expired lock
    now = datetime.now(timezone.utc)
    expired_iso = (now - timedelta(seconds=60)).isoformat()
    now_iso = now.isoformat()

    with sqlite3.connect(cleanup_repo.db_path) as conn:
        conn.execute(
            """INSERT INTO email_processing_state
               (mailbox_id, fingerprint, status, lock_owner, lock_expires_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("mailbox-1", "fp-1", WorkflowStatus.CLEANUP_PENDING.value, "worker-other", expired_iso, now_iso, now_iso)
        )
        record_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Test: Acquire lock (should succeed because it's expired)
    acquired = cleanup_repo.acquire_cleanup_lock(record_id, "worker-1", 60)
    assert acquired is True


def test_acquire_cleanup_lock_same_worker(cleanup_repo: SqliteCleanupRepository):
    # Setup: Create a record already locked by the same worker
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(seconds=60)).isoformat()

    with sqlite3.connect(cleanup_repo.db_path) as conn:
        conn.execute(
            """INSERT INTO email_processing_state
               (mailbox_id, fingerprint, status, lock_owner, lock_expires_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("mailbox-1", "fp-1", WorkflowStatus.CLEANUP_PENDING.value, "worker-1", expires_iso, now_iso, now_iso)
        )
        record_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Test: Re-acquire lock (should succeed)
    acquired = cleanup_repo.acquire_cleanup_lock(record_id, "worker-1", 60)
    assert acquired is True


def test_list_cleanup_pending(cleanup_repo: SqliteCleanupRepository):
    # Setup: Create records with various statuses
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cleanup_repo.db_path) as conn:
        conn.executemany(
            """INSERT INTO email_processing_state
               (mailbox_id, source_folder, fingerprint, sender, subject, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("m1", "src1", "fp1", "s", "s", WorkflowStatus.CLEANUP_PENDING.value, now, now),
                ("m1", "src1", "fp2", "s", "s", WorkflowStatus.CLEANUP_PENDING.value, now, now),
                ("m1", "src2", "fp3", "s", "s", WorkflowStatus.CLEANUP_PENDING.value, now, now),
                ("m1", "src1", "fp4", "s", "s", WorkflowStatus.PROCESSED.value, now, now),
                ("m2", "src1", "fp5", "s", "s", WorkflowStatus.CLEANUP_PENDING.value, now, now),
            ]
        )

    # Test: List pending for m1/src1
    pending = cleanup_repo.list_cleanup_pending("m1", "src1")
    assert len(pending) == 2
    assert {r.fingerprint for r in pending} == {"fp1", "fp2"}


def test_mark_move_cleanup_pending(cleanup_repo: SqliteCleanupRepository):
    # Setup: Create a record in PROCESSING state
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cleanup_repo.db_path) as conn:
        conn.execute(
            """INSERT INTO email_processing_state
               (mailbox_id, fingerprint, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("mailbox-1", "fp-1", WorkflowStatus.PROCESSING.value, now, now)
        )
        record_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Test: Mark as cleanup pending
    cleanup_repo.mark_move_cleanup_pending(
        record_id=record_id,
        category="cat",
        confidence=0.9,
        target_folder="target",
        target_uid="123",
        draft_path=None,
        rule_hit=None,
        model_name="gpt-4",
        model_latency_ms=100,
        error_message="move failed",
        error_type="MoveError"
    )

    # Verify
    with sqlite3.connect(cleanup_repo.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM email_processing_state WHERE id = ?", (record_id,)).fetchone()
        assert row["status"] == WorkflowStatus.CLEANUP_PENDING.value
        assert row["error_message"] == "move failed"
        assert row["last_error_type"] == "MoveError"


def test_mark_cleanup_completed(cleanup_repo: SqliteCleanupRepository):
    # Setup: Create a record in CLEANUP_PENDING state
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cleanup_repo.db_path) as conn:
        conn.execute(
            """INSERT INTO email_processing_state
               (mailbox_id, fingerprint, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("mailbox-1", "fp-1", WorkflowStatus.CLEANUP_PENDING.value, now, now)
        )
        record_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Test: Mark as completed
    cleanup_repo.mark_cleanup_completed(record_id)

    # Verify
    with sqlite3.connect(cleanup_repo.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM email_processing_state WHERE id = ?", (record_id,)).fetchone()
        assert row["status"] == WorkflowStatus.PROCESSED.value


# --- Lease Repository Tests ---

def test_acquire_lease_new_message(lease_repo: SqliteLeaseRepository):
    result = lease_repo.acquire_lease(
        mailbox_id="m1",
        message_id="msg1",
        fingerprint="fp1",
        content_fingerprint="cfp1",
        imap_uid="100",
        uidvalidity="1",
        sender="s@e.com",
        sender_sha256=None,
        subject="sub",
        subject_sha256=None,
        source_folder="INBOX",
        internaldate=None,
        worker_id="w1",
        lease_seconds=60,
        max_retries=3
    )

    assert result.outcome == "acquired"
    assert result.record is not None
    assert result.record.status == WorkflowStatus.PROCESSING
    assert result.record.lock_owner == "w1"


def test_acquire_lease_already_locked(lease_repo: SqliteLeaseRepository):
    # First acquisition
    lease_repo.acquire_lease(
        mailbox_id="m1", message_id="msg1", fingerprint="fp1",
        content_fingerprint=None, imap_uid="100", uidvalidity="1",
        sender="s@e.com", sender_sha256=None, subject="sub", subject_sha256=None,
        source_folder="INBOX", internaldate=None,
        worker_id="w1", lease_seconds=600, max_retries=3
    )

    # Second acquisition by different worker
    result = lease_repo.acquire_lease(
        mailbox_id="m1", message_id="msg1", fingerprint="fp1",
        content_fingerprint=None, imap_uid="100", uidvalidity="1",
        sender="s@e.com", sender_sha256=None, subject="sub", subject_sha256=None,
        source_folder="INBOX", internaldate=None,
        worker_id="w2", lease_seconds=600, max_retries=3
    )

    assert result.outcome == "locked"


# --- Worker Lock Repository Tests ---

def test_worker_lock_lifecycle(worker_lock_repo: SqliteWorkerLockRepository):
    # 1. Acquire
    r1 = worker_lock_repo.acquire_worker_lock("w1", 60)
    assert r1.acquired is True
    assert r1.lock_owner == "w1"

    # 2. Try acquire by another worker (should fail)
    r2 = worker_lock_repo.acquire_worker_lock("w2", 60)
    assert r2.acquired is False
    assert r2.lock_owner == "w1"

    # 3. Refresh by same worker
    success = worker_lock_repo.refresh_worker_lock("w1", 120)
    assert success is True

    # 4. Release
    worker_lock_repo.release_worker_lock("w1")

    # 5. Acquire by another worker (should succeed now)
    r3 = worker_lock_repo.acquire_worker_lock("w2", 60)
    assert r3.acquired is True
    assert r3.lock_owner == "w2"


def test_worker_lock_expired_takeover(worker_lock_repo: SqliteWorkerLockRepository):
    # Setup: expired lock
    now = datetime.now(timezone.utc)
    expired_iso = (now - timedelta(seconds=60)).isoformat()
    now_iso = now.isoformat()

    with sqlite3.connect(worker_lock_repo.db_path) as conn:
        conn.execute(
            """INSERT INTO worker_runtime_lock (lock_name, lock_owner, lock_expires_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            ("main", "w1", expired_iso, now_iso)
        )

    # Test: w2 should be able to take over
    result = worker_lock_repo.acquire_worker_lock("w2", 60)
    assert result.acquired is True
    assert result.lock_owner == "w2"
