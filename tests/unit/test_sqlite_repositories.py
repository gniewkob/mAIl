from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from mail_ai_agent.constants import WorkflowStatus
from mail_ai_agent.repositories.sqlite_repositories import (
    SqliteCleanupRepository,
    SqliteLeaseRepository,
    SqliteWorkerLockRepository,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_repos.sqlite"


@pytest.fixture
def setup_db(db_path: Path) -> None:
    """Initialize the database schema for repositories."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS email_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mailbox_id TEXT NOT NULL,
            message_id TEXT,
            fingerprint TEXT NOT NULL,
            content_fingerprint TEXT,
            imap_uid TEXT,
            uidvalidity TEXT,
            source_folder TEXT,
            target_folder TEXT,
            target_uid TEXT,
            sender TEXT,
            sender_sha256 TEXT,
            subject TEXT,
            subject_sha256 TEXT,
            internaldate TEXT,
            status TEXT NOT NULL,
            category TEXT,
            confidence REAL,
            action_taken TEXT,
            draft_path TEXT,
            error_message TEXT,
            processing_started_at TEXT,
            lock_expires_at TEXT,
            lock_owner TEXT,
            attempt_count INTEGER DEFAULT 0,
            last_error_at TEXT,
            last_error_type TEXT,
            rule_hit TEXT,
            model_name TEXT,
            model_latency_ms INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS worker_locks (
            lock_name TEXT PRIMARY KEY,
            worker_id TEXT NOT NULL,
            lock_expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def test_worker_lock_acquisition(db_path: Path, setup_db: None) -> None:
    repo = SqliteWorkerLockRepository(db_path)

    # First acquisition
    result = repo.acquire_worker_lock(worker_id="worker-1", lease_seconds=60)
    assert result.acquired is True
    assert result.lock_owner == "worker-1"

    # Second acquisition by same worker (refresh)
    result2 = repo.acquire_worker_lock(worker_id="worker-1", lease_seconds=60)
    assert result2.acquired is True
    assert result2.lock_owner == "worker-1"

    # Different worker blocked
    result3 = repo.acquire_worker_lock(worker_id="worker-2", lease_seconds=60)
    assert result3.acquired is False
    assert result3.lock_owner == "worker-1"


def test_worker_lock_expiration(db_path: Path, setup_db: None) -> None:
    repo = SqliteWorkerLockRepository(db_path)

    # Acquire with negative lease (already expired)
    repo.acquire_worker_lock(worker_id="worker-1", lease_seconds=-1)

    # Different worker should be able to acquire it
    result = repo.acquire_worker_lock(worker_id="worker-2", lease_seconds=60)
    assert result.acquired is True
    assert result.lock_owner == "worker-2"


def test_worker_lock_release(db_path: Path, setup_db: None) -> None:
    repo = SqliteWorkerLockRepository(db_path)

    repo.acquire_worker_lock(worker_id="worker-1", lease_seconds=60)
    repo.release_worker_lock(worker_id="worker-1")

    # Different worker can now acquire
    result = repo.acquire_worker_lock(worker_id="worker-2", lease_seconds=60)
    assert result.acquired is True
    assert result.lock_owner == "worker-2"


def test_worker_lock_refresh(db_path: Path, setup_db: None) -> None:
    repo = SqliteWorkerLockRepository(db_path)

    repo.acquire_worker_lock(worker_id="worker-1", lease_seconds=60)
    success = repo.refresh_worker_lock(worker_id="worker-1", lease_seconds=120)
    assert success is True


def test_cleanup_lock_acquisition(db_path: Path, setup_db: None) -> None:
    repo = SqliteCleanupRepository(db_path)

    # Insert a record in cleanup_pending state
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO email_state (mailbox_id, fingerprint, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("mb1", "fp1", WorkflowStatus.CLEANUP_PENDING.value, "now", "now")
    )
    conn.commit()
    row = conn.execute("SELECT id FROM email_state WHERE fingerprint = 'fp1'").fetchone()
    record_id = row[0]
    conn.close()

    # Acquire cleanup lock
    acquired = repo.acquire_cleanup_lock(record_id=record_id, worker_id="worker-1", lease_seconds=60)
    assert acquired is True

    # Second acquisition by different worker fails
    acquired2 = repo.acquire_cleanup_lock(record_id=record_id, worker_id="worker-2", lease_seconds=60)
    assert acquired2 is False


def test_lease_acquisition_locking(db_path: Path, setup_db: None) -> None:
    repo = SqliteLeaseRepository(db_path)

    # First acquisition
    result = repo.acquire_lease(
        mailbox_id="mb1",
        message_id="msg1",
        fingerprint="fp1",
        content_fingerprint="cfp1",
        imap_uid="1",
        uidvalidity="123",
        sender="test@example.com",
        sender_sha256="s1",
        subject="Test",
        subject_sha256="sub1",
        source_folder="INBOX",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3
    )
    assert result.outcome == "acquired"
    assert result.record.lock_owner == "worker-1"

    # Second worker blocked
    result2 = repo.acquire_lease(
        mailbox_id="mb1",
        message_id="msg1",
        fingerprint="fp1",
        content_fingerprint="cfp1",
        imap_uid="1",
        uidvalidity="123",
        sender="test@example.com",
        sender_sha256="s1",
        subject="Test",
        subject_sha256="sub1",
        source_folder="INBOX",
        internaldate=None,
        worker_id="worker-2",
        lease_seconds=60,
        max_retries=3
    )
    assert result2.outcome == "locked"
