"""StateManager v2 - refactored to use repository pattern.

This version separates concerns into specialized repositories:
- LeaseRepository: lease acquisition and management
- StateRepository: email state transitions
- CleanupRepository: cleanup operations
- WorkerLockRepository: global worker lock management
"""

from __future__ import annotations

from pathlib import Path

from .constants import MOVE_CLEANUP_PENDING_ACTION
from .repositories import (
    SqliteCleanupRepository,
    SqliteLeaseRepository,
    SqliteStateRepository,
    SqliteWorkerLockRepository,
)
from .repositories.fake_repositories import (
    FakeStateManager,
)
from .state_manager_base import BaseStateManager
from .utils import _chmod_owner_only

__all__ = ["StateManagerV2", "FakeStateManager", "MOVE_CLEANUP_PENDING_ACTION"]


class StateManagerV2(BaseStateManager):
    """State manager using repository pattern for better testability.
    
    This class provides the same interface as the original StateManager
    but delegates to specialized repositories internally.
    """
    
    def __init__(self, db_path: Path, *, _sqlite: bool = True) -> None:
        """Initialize state manager.
        
        Args:
            db_path: Path to SQLite database (ignored if _sqlite=False)
            _sqlite: If True, use SQLite repositories; if False, create uninitialized manager
                      (for creating FakeStateManager instances)
        """
        if _sqlite:
            self.db_path = db_path
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            _chmod_owner_only(self.db_path.parent)
            
            # Initialize repositories
            self._leases = SqliteLeaseRepository(db_path)
            self._state = SqliteStateRepository(db_path)
            self._cleanup = SqliteCleanupRepository(db_path)
            self._worker_locks = SqliteWorkerLockRepository(db_path)
            
            # Initialize schema (delegated to repositories via _initialize)
            self._initialize()
            _chmod_owner_only(self.db_path)
        else:
            # Uninitialized - for internal use
            self.db_path = db_path
            self._leases = None  # type: ignore
            self._state = None  # type: ignore
            self._cleanup = None  # type: ignore
            self._worker_locks = None  # type: ignore
    
    def _initialize(self) -> None:
        """Initialize database schema.
        
        Schema is managed by repositories individually.
        This method ensures all tables exist.
        """
        # Schema initialization happens in repository constructors
        # when they first connect to the database
        import sqlite3
        
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS email_processing_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mailbox_id TEXT NOT NULL DEFAULT 'default',
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
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error_at TEXT,
                    last_error_type TEXT,
                    rule_hit TEXT,
                    model_name TEXT,
                    model_latency_ms INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_runtime_lock (
                    lock_name TEXT PRIMARY KEY,
                    lock_owner TEXT NOT NULL,
                    lock_expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_email_mailbox_fingerprint
                    ON email_processing_state(mailbox_id, fingerprint);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_email_mailbox_message_id_not_null
                    ON email_processing_state(mailbox_id, message_id) WHERE message_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_email_status ON email_processing_state(status);
                CREATE INDEX IF NOT EXISTS idx_email_mailbox_source ON email_processing_state(mailbox_id, source_folder);
                """
            )
            
            # Migration: add columns if they don't exist
            columns = {row[1] for row in conn.execute("PRAGMA table_info(email_processing_state)")}
            if "mailbox_id" not in columns:
                conn.execute("ALTER TABLE email_processing_state ADD COLUMN mailbox_id TEXT NOT NULL DEFAULT 'default'")
            if "content_fingerprint" not in columns:
                conn.execute("ALTER TABLE email_processing_state ADD COLUMN content_fingerprint TEXT")
            if "sender_sha256" not in columns:
                conn.execute("ALTER TABLE email_processing_state ADD COLUMN sender_sha256 TEXT")
            if "subject_sha256" not in columns:
                conn.execute("ALTER TABLE email_processing_state ADD COLUMN subject_sha256 TEXT")
            
            conn.commit()
        finally:
            conn.close()
