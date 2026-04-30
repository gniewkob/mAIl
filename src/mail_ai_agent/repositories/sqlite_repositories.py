"""SQLite implementations of repository protocols."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..constants import WorkflowStatus
from ..db_pool import ConnectionPool, get_pool
from ..schemas import EmailRecord, LeaseAcquireResult, WorkerLockResult
from ..utils import _hash_value

from .base import (
    CleanupRepositoryProtocol,
    LeaseRepositoryProtocol,
    StateRepositoryProtocol,
    WorkerLockRepositoryProtocol,
)


class SqliteBaseRepository:
    """Base class for SQLite repositories using connection pooling."""
    
    def __init__(self, db_path: Path, max_connections: int = 5) -> None:
        self.db_path = db_path
        self._pool = get_pool(db_path, max_connections)
    
    def _connect(self) -> ConnectionPool:
        """Get connection pool context manager."""
        return self._pool.connection()
    
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
    def _row_to_record(self, row: sqlite3.Row) -> EmailRecord:
        """Convert database row to EmailRecord."""
        return EmailRecord(
            id=row["id"],
            mailbox_id=row["mailbox_id"],
            message_id=row["message_id"],
            fingerprint=row["fingerprint"],
            content_fingerprint=row["content_fingerprint"],
            imap_uid=row["imap_uid"],
            uidvalidity=row["uidvalidity"],
            source_folder=row["source_folder"],
            target_folder=row["target_folder"],
            target_uid=row["target_uid"],
            sender=row["sender"],
            sender_sha256=row["sender_sha256"],
            subject=row["subject"],
            subject_sha256=row["subject_sha256"],
            internaldate=row["internaldate"],
            status=WorkflowStatus(row["status"]) if row["status"] else WorkflowStatus.PROCESSING,
            category=row["category"],
            confidence=row["confidence"],
            action_taken=row["action_taken"],
            draft_path=row["draft_path"],
            error_message=row["error_message"],
            processing_started_at=row["processing_started_at"],
            lock_expires_at=row["lock_expires_at"],
            lock_owner=row["lock_owner"],
            attempt_count=row["attempt_count"],
            last_error_at=row["last_error_at"],
            last_error_type=row["last_error_type"],
            rule_hit=row["rule_hit"],
            model_name=row["model_name"],
            model_latency_ms=row["model_latency_ms"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class SqliteLeaseRepository(SqliteBaseRepository, LeaseRepositoryProtocol):
    """SQLite implementation of lease acquisition logic."""
    
    def acquire_lease(
        self,
        *,
        mailbox_id: str,
        message_id: str | None,
        fingerprint: str,
        content_fingerprint: str | None,
        imap_uid: str | None,
        uidvalidity: str | None,
        sender: str,
        sender_sha256: str | None,
        subject: str,
        subject_sha256: str | None,
        source_folder: str,
        internaldate: str | None,
        worker_id: str,
        lease_seconds: int,
        max_retries: int,
    ) -> LeaseAcquireResult:
        """Attempt to acquire a processing lease for a message."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Check for existing record by fingerprint
            cursor.execute(
                """SELECT id, status, message_id, uidvalidity, lock_expires_at, lock_owner, 
                          attempt_count, last_error_at, last_error_type
                   FROM email_state 
                   WHERE mailbox_id = ? AND fingerprint = ?""",
                (mailbox_id, fingerprint),
            )
            row = cursor.fetchone()
            
            if row is not None:
                return self._handle_existing_record(
                    conn, cursor, row, mailbox_id, fingerprint, uidvalidity,
                    message_id, worker_id, expires, now_iso, max_retries
                )
            
            # Check for identity conflicts
            conflict_result = self._check_identity_conflicts(
                conn, cursor, mailbox_id, message_id, fingerprint, sender_sha256, subject_sha256
            )
            if conflict_result:
                return conflict_result
            
            # Insert new record and acquire lease
            return self._insert_new_record(
                conn, cursor, mailbox_id, message_id, fingerprint, content_fingerprint,
                imap_uid, uidvalidity, sender, sender_sha256, subject, subject_sha256,
                source_folder, internaldate, worker_id, expires, now_iso
            )
    
    def _handle_existing_record(
        self, conn, cursor, row, mailbox_id, fingerprint, uidvalidity,
        message_id, worker_id, expires, now_iso, max_retries
    ) -> LeaseAcquireResult:
        """Handle case when record already exists."""
        record = self._row_to_record(row)
        
        # Check for UIDVALIDITY mismatch
        if record.uidvalidity and uidvalidity and record.uidvalidity != uidvalidity:
            return LeaseAcquireResult(
                outcome="conflict",
                record=record,
                reason="uidvalidity changed"
            )
        
        # Check if already in terminal state
        if record.status in {WorkflowStatus.PROCESSED, WorkflowStatus.SKIPPED, WorkflowStatus.UNCERTAIN}:
            return LeaseAcquireResult(
                outcome="already_done",
                record=record,
                reason=f"message already {record.status.value}"
            )
        
        # Check max retries
        if record.attempt_count >= max_retries:
            cursor.execute(
                """UPDATE email_state 
                   SET status = ?, updated_at = ?, lock_owner = NULL, lock_expires_at = NULL
                   WHERE id = ?""",
                (WorkflowStatus.FAILED.value, now_iso, record.id)
            )
            conn.commit()
            return LeaseAcquireResult(
                outcome="already_done",
                record=record,
                reason="max retries exceeded"
            )
        
        # Try to acquire/expired lease
        if record.lock_expires_at and record.lock_expires_at < now_iso:
            cursor.execute(
                """UPDATE email_state 
                   SET lock_owner = ?, lock_expires_at = ?, updated_at = ?, processing_started_at = ?
                   WHERE id = ?""",
                (worker_id, expires, now_iso, now_iso, record.id)
            )
            conn.commit()
            record.lock_owner = worker_id
            record.lock_expires_at = expires
            return LeaseAcquireResult(outcome="acquired", record=record, reason="expired lease taken")
        
        if record.lock_owner:
            return LeaseAcquireResult(
                outcome="locked",
                record=record,
                reason="active lease exists"
            )
        
        return LeaseAcquireResult(
            outcome="conflict",
            record=record,
            reason="message identity conflict"
        )
    
    def _check_identity_conflicts(
        self, conn, cursor, mailbox_id, message_id, fingerprint, sender_sha256, subject_sha256
    ) -> LeaseAcquireResult | None:
        """Check for message identity conflicts."""
        if not message_id:
            return None
        
        cursor.execute(
            """SELECT * FROM email_state 
               WHERE mailbox_id = ? AND message_id = ?""",
            (mailbox_id, message_id)
        )
        identity_rows = [cursor.fetchone()]
        
        if sender_sha256 and subject_sha256:
            cursor.execute(
                """SELECT * FROM email_state 
                   WHERE mailbox_id = ? AND sender_sha256 = ? AND subject_sha256 = ?""",
                (mailbox_id, sender_sha256, subject_sha256)
            )
            identity_rows.append(cursor.fetchone())
        
        row = next((r for r in identity_rows if r is not None), None)
        if row is not None:
            record = self._row_to_record(row)
            if record.fingerprint != fingerprint:
                return LeaseAcquireResult(
                    outcome="conflict",
                    record=record,
                    reason="message identity conflict"
                )
        
        return None
    
    def _insert_new_record(
        self, conn, cursor, mailbox_id, message_id, fingerprint, content_fingerprint,
        imap_uid, uidvalidity, sender, sender_sha256, subject, subject_sha256,
        source_folder, internaldate, worker_id, expires, now_iso
    ) -> LeaseAcquireResult:
        """Insert new record with lease."""
        cursor.execute(
            """INSERT INTO email_state 
                (mailbox_id, message_id, fingerprint, content_fingerprint,
                 imap_uid, uidvalidity, sender, sender_sha256, subject, subject_sha256,
                 source_folder, internaldate, status, lock_owner, lock_expires_at,
                 processing_started_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mailbox_id, message_id, fingerprint, content_fingerprint,
                imap_uid, uidvalidity, sender, sender_sha256, subject, subject_sha256,
                source_folder, internaldate, WorkflowStatus.PROCESSING.value,
                worker_id, expires, now_iso, now_iso, now_iso
            )
        )
        conn.commit()
        
        record = self._row_to_record(cursor.execute(
            "SELECT * FROM email_state WHERE id = ?",
            (cursor.lastrowid,)
        ).fetchone())
        
        return LeaseAcquireResult(outcome="acquired", record=record, reason="new lease acquired")
    
    def is_message_processed(
        self,
        mailbox_id: str,
        fingerprint: str,
    ) -> EmailRecord | None:
        """Check if message was already processed."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM email_state 
                   WHERE mailbox_id = ? AND fingerprint = ? 
                   AND status IN (?, ?, ?)""",
                (mailbox_id, fingerprint, 
                 WorkflowStatus.PROCESSED.value, 
                 WorkflowStatus.UNCERTAIN.value,
                 WorkflowStatus.SKIPPED.value)
            )
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None
    
    def get_active_lease(
        self,
        mailbox_id: str,
        fingerprint: str,
    ) -> EmailRecord | None:
        """Get existing active lease if any."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM email_state 
                   WHERE mailbox_id = ? AND fingerprint = ? 
                   AND lock_expires_at > ?""",
                (mailbox_id, fingerprint, now)
            )
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None


class SqliteStateRepository(SqliteBaseRepository, StateRepositoryProtocol):
    """SQLite implementation of state transitions."""
    
    def mark_processed(
        self,
        record_id: int,
        *,
        category: str,
        confidence: float | None,
        target_folder: str,
        target_uid: str | None,
        action_taken: str,
        draft_path: str | None,
        rule_hit: str | None,
        model_name: str | None,
        model_latency_ms: int | None,
    ) -> None:
        """Mark record as successfully processed."""
        now = self._now()
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE email_state 
                   SET status = ?, category = ?, confidence = ?, target_folder = ?,
                       target_uid = ?, action_taken = ?, draft_path = ?, rule_hit = ?,
                       model_name = ?, model_latency_ms = ?, lock_owner = NULL,
                       lock_expires_at = NULL, updated_at = ?, attempt_count = attempt_count + 1
                   WHERE id = ?""",
                (WorkflowStatus.PROCESSED.value, category, confidence, target_folder,
                 target_uid, action_taken, draft_path, rule_hit, model_name, model_latency_ms,
                 now, record_id)
            )
            conn.commit()
    
    def mark_uncertain(
        self,
        record_id: int,
        *,
        category: str,
        confidence: float | None,
        target_folder: str,
        target_uid: str | None,
        action_taken: str,
        error_message: str | None = None,
    ) -> None:
        """Mark record as uncertain."""
        now = self._now()
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE email_state 
                   SET status = ?, category = ?, confidence = ?, target_folder = ?,
                       target_uid = ?, action_taken = ?, error_message = ?, lock_owner = NULL,
                       lock_expires_at = NULL, updated_at = ?, attempt_count = attempt_count + 1
                   WHERE id = ?""",
                (WorkflowStatus.UNCERTAIN.value, category, confidence, target_folder,
                 target_uid, action_taken, error_message, now, record_id)
            )
            conn.commit()
    
    def mark_failed(
        self,
        record_id: int,
        *,
        error_message: str,
        error_type: str | None = None,
    ) -> None:
        """Mark record as failed."""
        now = self._now()
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE email_state 
                   SET status = ?, error_message = ?, last_error_type = ?, last_error_at = ?,
                       lock_owner = NULL, lock_expires_at = NULL, updated_at = ?,
                       attempt_count = attempt_count + 1
                   WHERE id = ?""",
                (WorkflowStatus.FAILED.value, error_message, error_type, now, now, record_id)
            )
            conn.commit()
    
    def get_by_id(self, record_id: int) -> EmailRecord | None:
        """Get record by ID."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM email_state WHERE id = ?", (record_id,))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None
    
    def get_by_message_id(
        self,
        mailbox_id: str,
        message_id: str,
    ) -> EmailRecord | None:
        """Get record by message ID."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM email_state WHERE mailbox_id = ? AND message_id = ?",
                (mailbox_id, message_id)
            )
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None
    
    def get_by_fingerprint(
        self,
        mailbox_id: str,
        fingerprint: str,
    ) -> EmailRecord | None:
        """Get record by fingerprint."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM email_state WHERE mailbox_id = ? AND fingerprint = ?",
                (mailbox_id, fingerprint)
            )
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None


class SqliteCleanupRepository(SqliteBaseRepository, CleanupRepositoryProtocol):
    """SQLite implementation of cleanup operations."""
    
    def list_cleanup_pending(
        self,
        mailbox_id: str,
        source_folder: str,
    ) -> list[EmailRecord]:
        """List records with cleanup_pending status."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM email_state 
                   WHERE mailbox_id = ? AND source_folder = ? 
                   AND status = ?
                   ORDER BY id""",
                (mailbox_id, source_folder, WorkflowStatus.CLEANUP_PENDING.value)
            )
            return [self._row_to_record(row) for row in cursor.fetchall()]
    
    def mark_move_cleanup_pending(
        self,
        record_id: int,
        *,
        category: str,
        confidence: float | None,
        target_folder: str,
        target_uid: str | None,
        draft_path: str | None,
        rule_hit: str | None,
        model_name: str | None,
        model_latency_ms: int | None,
        error_message: str,
        error_type: str | None,
    ) -> None:
        """Mark record as cleanup_pending."""
        now = self._now()
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE email_state 
                   SET status = ?, category = ?, confidence = ?, target_folder = ?,
                       target_uid = ?, draft_path = ?, rule_hit = ?, model_name = ?,
                       model_latency_ms = ?, error_message = ?, last_error_type = ?,
                       last_error_at = ?, lock_owner = NULL, lock_expires_at = NULL,
                       updated_at = ?, attempt_count = attempt_count + 1
                   WHERE id = ?""",
                (WorkflowStatus.CLEANUP_PENDING.value, category, confidence, target_folder,
                 target_uid, draft_path, rule_hit, model_name, model_latency_ms,
                 error_message, error_type, now, now, record_id)
            )
            conn.commit()
    
    def mark_cleanup_completed(self, record_id: int) -> None:
        """Mark cleanup as completed."""
        now = self._now()
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE email_state 
                   SET status = ?, updated_at = ?
                   WHERE id = ?""",
                (WorkflowStatus.PROCESSED.value, now, record_id)
            )
            conn.commit()
    
    def acquire_cleanup_lock(
        self,
        record_id: int,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        """Try to acquire lock for cleanup operation."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Try to acquire lock
            cursor.execute(
                """UPDATE email_state 
                   SET lock_owner = ?, lock_expires_at = ?, updated_at = ?
                   WHERE id = ? AND (lock_owner IS NULL OR lock_owner = ? OR lock_expires_at < ?)""",
                (worker_id, expires, now_iso, record_id, worker_id, now_iso)
            )
            conn.commit()
            
            return cursor.rowcount > 0


class SqliteWorkerLockRepository(SqliteBaseRepository, WorkerLockRepositoryProtocol):
    """SQLite implementation of worker lock management."""
    
    def acquire_worker_lock(
        self,
        worker_id: str,
        lease_seconds: int,
    ) -> WorkerLockResult:
        """Acquire global worker lock."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Ensure worker_locks table exists
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS worker_locks (
                    id INTEGER PRIMARY KEY,
                    worker_id TEXT UNIQUE NOT NULL,
                    lock_expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            
            # Try to acquire lock
            cursor.execute(
                """INSERT INTO worker_locks (worker_id, lock_expires_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(worker_id) DO UPDATE SET
                       lock_expires_at = excluded.lock_expires_at,
                       updated_at = excluded.updated_at
                   WHERE worker_locks.lock_expires_at < ?""",
                (worker_id, expires, now_iso, now_iso, now_iso)
            )
            conn.commit()
            
            if cursor.rowcount > 0:
                return WorkerLockResult(acquired=True, lock_owner=worker_id, reason="lock acquired")
            
            # Check who holds the lock
            cursor.execute(
                "SELECT worker_id, lock_expires_at FROM worker_locks WHERE id = 1"
            )
            row = cursor.fetchone()
            if row:
                return WorkerLockResult(
                    acquired=False,
                    lock_owner=row["worker_id"],
                    reason=f"lock held by {row['worker_id']} until {row['lock_expires_at']}"
                )
            
            return WorkerLockResult(acquired=False, lock_owner=None, reason="unknown lock conflict")
    
    def release_worker_lock(self, worker_id: str) -> None:
        """Release global worker lock."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM worker_locks WHERE worker_id = ?",
                (worker_id,)
            )
            conn.commit()
    
    def refresh_worker_lock(self, worker_id: str, lease_seconds: int) -> bool:
        """Refresh worker lock to prevent expiration."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE worker_locks 
                   SET lock_expires_at = ?, updated_at = ?
                   WHERE worker_id = ?""",
                (expires, now_iso, worker_id)
            )
            conn.commit()
            return cursor.rowcount > 0
