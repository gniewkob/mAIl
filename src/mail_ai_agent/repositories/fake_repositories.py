"""Fake/in-memory implementations of repository protocols for testing.

These implementations are much faster than SQLite for unit tests.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from ..constants import WorkflowStatus
from ..schemas import EmailRecord, LeaseAcquireResult, WorkerLockResult
from .base import (
    CleanupRepositoryProtocol,
    LeaseRepositoryProtocol,
    StateRepositoryProtocol,
    WorkerLockRepositoryProtocol,
)


class FakeLeaseRepository(LeaseRepositoryProtocol):
    """In-memory fake implementation of lease repository."""
    
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], EmailRecord] = {}
        self._id_counter = 0
        self._lock = threading.RLock()  # RLock for nested acquire
    
    def _next_id(self) -> int:
        with self._lock:
            self._id_counter += 1
            return self._id_counter
    
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
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
        """Attempt to acquire a processing lease."""
        key = (mailbox_id, fingerprint)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        
        with self._lock:
            existing = self._records.get(key)
            
            if existing:
                # Check terminal states
                if existing.status in {WorkflowStatus.PROCESSED, WorkflowStatus.SKIPPED, WorkflowStatus.UNCERTAIN}:
                    return LeaseAcquireResult(
                        outcome="already_done",
                        record=existing,
                        reason=f"message already {existing.status.value}"
                    )
                
                # Check max retries
                if existing.attempt_count >= max_retries:
                    existing.status = WorkflowStatus.FAILED
                    existing.updated_at = now_iso
                    return LeaseAcquireResult(
                        outcome="already_done",
                        record=existing,
                        reason="max retries exceeded"
                    )
                
                # Check active lease
                if existing.lock_expires_at and existing.lock_expires_at > now_iso:
                    return LeaseAcquireResult(
                        outcome="locked",
                        record=existing,
                        reason="active lease exists"
                    )
                
                # Take expired lease
                existing.lock_owner = worker_id
                existing.lock_expires_at = expires
                existing.processing_started_at = now_iso
                existing.updated_at = now_iso
                return LeaseAcquireResult(outcome="acquired", record=existing, reason="expired lease taken")
            
            # Create new record
            record_id = self._next_id()
            record = EmailRecord(
                id=record_id,
                mailbox_id=mailbox_id,
                message_id=message_id,
                fingerprint=fingerprint,
                content_fingerprint=content_fingerprint,
                imap_uid=imap_uid,
                uidvalidity=uidvalidity,
                source_folder=source_folder,
                target_folder=None,
                target_uid=None,
                sender=sender,
                sender_sha256=sender_sha256,
                subject=subject,
                subject_sha256=subject_sha256,
                internaldate=internaldate,
                status=WorkflowStatus.PROCESSING,
                category=None,
                confidence=None,
                action_taken=None,
                draft_path=None,
                error_message=None,
                processing_started_at=now_iso,
                lock_expires_at=expires,
                lock_owner=worker_id,
                attempt_count=0,
                last_error_at=None,
                last_error_type=None,
                rule_hit=None,
                model_name=None,
                model_latency_ms=None,
                created_at=now_iso,
                updated_at=now_iso,
            )
            self._records[key] = record
            return LeaseAcquireResult(outcome="acquired", record=record, reason="new lease acquired")
    
    def is_message_processed(
        self,
        mailbox_id: str,
        fingerprint: str,
    ) -> EmailRecord | None:
        """Check if message was already processed."""
        key = (mailbox_id, fingerprint)
        record = self._records.get(key)
        if record and record.status in {WorkflowStatus.PROCESSED, WorkflowStatus.SKIPPED, WorkflowStatus.UNCERTAIN}:
            return record
        return None
    
    def get_active_lease(
        self,
        mailbox_id: str,
        fingerprint: str,
    ) -> EmailRecord | None:
        """Get existing active lease if any."""
        key = (mailbox_id, fingerprint)
        record = self._records.get(key)
        now = datetime.now(timezone.utc).isoformat()
        if record and record.lock_expires_at and record.lock_expires_at > now:
            return record
        return None


class FakeStateRepository(StateRepositoryProtocol):
    """In-memory fake implementation of state repository."""
    
    def __init__(self, records: dict[tuple[str, str], EmailRecord] | None = None) -> None:
        self._records = records if records is not None else {}
    
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
    def _find_by_id(self, record_id: int) -> tuple[tuple[str, str], EmailRecord] | None:
        for key, record in self._records.items():
            if record.id == record_id:
                return key, record
        return None
    
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
        result = self._find_by_id(record_id)
        if not result:
            LOGGER.warning("mark_processed: Record %s not found", record_id)
            raise ValueError(f"Record {record_id} not found")
        
        _, record = result
        record.status = WorkflowStatus.PROCESSED
        record.category = category
        record.confidence = confidence
        record.target_folder = target_folder
        record.target_uid = target_uid
        record.action_taken = action_taken
        record.draft_path = draft_path
        record.rule_hit = rule_hit
        record.model_name = model_name
        record.model_latency_ms = model_latency_ms
        record.lock_owner = None
        record.lock_expires_at = None
        record.updated_at = self._now()
        record.attempt_count += 1
    
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
        result = self._find_by_id(record_id)
        if not result:
            LOGGER.warning("mark_uncertain: Record %s not found", record_id)
            raise ValueError(f"Record {record_id} not found")
        
        _, record = result
        record.status = WorkflowStatus.UNCERTAIN
        record.category = category
        record.confidence = confidence
        record.target_folder = target_folder
        record.target_uid = target_uid
        record.action_taken = action_taken
        record.error_message = error_message
        record.lock_owner = None
        record.lock_expires_at = None
        record.updated_at = self._now()
        record.attempt_count += 1
    
    def mark_failed(
        self,
        record_id: int,
        *,
        error_message: str,
        error_type: str | None = None,
    ) -> None:
        """Mark record as failed."""
        result = self._find_by_id(record_id)
        if not result:
            LOGGER.warning("mark_failed: Record %s not found", record_id)
            raise ValueError(f"Record {record_id} not found")
        
        _, record = result
        record.status = WorkflowStatus.FAILED
        record.error_message = error_message
        record.last_error_type = error_type
        record.last_error_at = self._now()
        record.lock_owner = None
        record.lock_expires_at = None
        record.updated_at = self._now()
        record.attempt_count += 1
    
    def get_by_id(self, record_id: int) -> EmailRecord | None:
        """Get record by ID."""
        result = self._find_by_id(record_id)
        return result[1] if result else None
    
    def get_by_message_id(
        self,
        mailbox_id: str,
        message_id: str,
    ) -> EmailRecord | None:
        """Get record by message ID."""
        for record in self._records.values():
            if record.mailbox_id == mailbox_id and record.message_id == message_id:
                return record
        return None
    
    def get_by_fingerprint(
        self,
        mailbox_id: str,
        fingerprint: str,
    ) -> EmailRecord | None:
        """Get record by fingerprint."""
        return self._records.get((mailbox_id, fingerprint))


class FakeCleanupRepository(CleanupRepositoryProtocol):
    """In-memory fake implementation of cleanup repository."""
    
    def __init__(self, records: dict[tuple[str, str], EmailRecord] | None = None) -> None:
        self._records = records if records is not None else {}
        self._cleanup_locks: dict[int, dict[str, Any]] = {}
    
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
    def _find_by_id(self, record_id: int) -> tuple[tuple[str, str], EmailRecord] | None:
        for key, record in self._records.items():
            if record.id == record_id:
                return key, record
        return None
    
    def list_cleanup_pending(
        self,
        mailbox_id: str,
        source_folder: str,
    ) -> list[EmailRecord]:
        """List records with cleanup_pending status."""
        return [
            record for record in self._records.values()
            if record.mailbox_id == mailbox_id 
            and record.source_folder == source_folder
            and record.status == WorkflowStatus.CLEANUP_PENDING
        ]
    
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
        result = self._find_by_id(record_id)
        if not result:
            LOGGER.warning("mark_move_cleanup_pending: Record %s not found", record_id)
            raise ValueError(f"Record {record_id} not found")
        
        _, record = result
        record.status = WorkflowStatus.CLEANUP_PENDING
        record.category = category
        record.confidence = confidence
        record.target_folder = target_folder
        record.target_uid = target_uid
        record.draft_path = draft_path
        record.rule_hit = rule_hit
        record.model_name = model_name
        record.model_latency_ms = model_latency_ms
        record.error_message = error_message
        record.last_error_type = error_type
        record.last_error_at = self._now()
        record.lock_owner = None
        record.lock_expires_at = None
        record.updated_at = self._now()
        record.attempt_count += 1
    
    def mark_cleanup_completed(self, record_id: int) -> None:
        """Mark cleanup as completed."""
        result = self._find_by_id(record_id)
        if not result:
            LOGGER.warning("mark_cleanup_completed: Record %s not found", record_id)
            raise ValueError(f"Record {record_id} not found")
        
        _, record = result
        record.status = WorkflowStatus.PROCESSED
        record.updated_at = self._now()
    
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
        
        existing = self._cleanup_locks.get(record_id)
        
        if existing:
            if existing["owner"] == worker_id:
                existing["expires"] = expires
                return True
            if existing["expires"] > now_iso:
                return False
        
        self._cleanup_locks[record_id] = {
            "owner": worker_id,
            "expires": expires,
        }
        
        # Update record lock fields
        result = self._find_by_id(record_id)
        if result:
            _, record = result
            record.lock_owner = worker_id
            record.lock_expires_at = expires
        
        return True


class FakeWorkerLockRepository(WorkerLockRepositoryProtocol):
    """In-memory fake implementation of worker lock repository."""
    
    def __init__(self) -> None:
        self._lock: dict[str, Any] | None = None
    
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
    def acquire_worker_lock(
        self,
        worker_id: str,
        lease_seconds: int,
    ) -> WorkerLockResult:
        """Acquire global worker lock."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        
        if self._lock:
            if self._lock["expires"] > now_iso:
                return WorkerLockResult(
                    acquired=False,
                    lock_owner=self._lock["worker_id"],
                    reason=f"lock held by {self._lock['worker_id']} until {self._lock['expires']}"
                )
        
        self._lock = {
            "worker_id": worker_id,
            "expires": expires,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        
        return WorkerLockResult(acquired=True, lock_owner=worker_id, reason="lock acquired")
    
    def release_worker_lock(self, worker_id: str) -> None:
        """Release global worker lock."""
        if self._lock and self._lock["worker_id"] == worker_id:
            self._lock = None
    
    def refresh_worker_lock(self, worker_id: str, lease_seconds: int) -> bool:
        """Refresh worker lock to prevent expiration."""
        if not self._lock or self._lock["worker_id"] != worker_id:
            return False
        
        now = datetime.now(timezone.utc)
        self._lock["expires"] = (now + timedelta(seconds=lease_seconds)).isoformat()
        self._lock["updated_at"] = now.isoformat()
        return True


from ..state_manager_base import BaseStateManager


class FakeStateManager(BaseStateManager):
    """Combined fake state manager using all fake repositories.
    
    Drop-in replacement for StateManager in tests.
    Thread-safe for concurrent access.
    """
    
    def __init__(self, _db_path: Any = None) -> None:
        """Initialize with optional db_path (ignored, for API compatibility)."""
        self._leases = FakeLeaseRepository()
        self._state = FakeStateRepository(self._leases._records)
        self._cleanup = FakeCleanupRepository(self._leases._records)
        self._worker_locks = FakeWorkerLockRepository()
        # Shared lock for thread-safe access to _records
        self._lock = self._leases._lock
