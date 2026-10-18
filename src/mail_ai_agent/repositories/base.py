"""Repository protocols defining the interface for state management.

These protocols allow for easy testing with fake implementations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..schemas import EmailRecord, LeaseAcquireResult, WorkerLockResult


@runtime_checkable
class LeaseRepositoryProtocol(Protocol):
    """Protocol for lease acquisition and management."""
    
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
        ...
    
    def is_message_processed(
        self,
        mailbox_id: str,
        fingerprint: str,
    ) -> EmailRecord | None:
        """Check if message was already processed (idempotency check)."""
        ...
    
    def get_active_lease(
        self,
        mailbox_id: str,
        fingerprint: str,
    ) -> EmailRecord | None:
        """Get existing active lease if any."""
        ...


@runtime_checkable
class StateRepositoryProtocol(Protocol):
    """Protocol for email state transitions."""
    
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
        ...
    
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
        """Mark record as uncertain (needs human review)."""
        ...
    
    def mark_failed(
        self,
        record_id: int,
        *,
        error_message: str,
        error_type: str | None = None,
    ) -> None:
        """Mark record as failed with error details."""
        ...
    
    def get_by_id(self, record_id: int) -> EmailRecord | None:
        """Get record by ID."""
        ...
    
    def get_by_message_id(
        self,
        mailbox_id: str,
        message_id: str,
    ) -> EmailRecord | None:
        """Get record by message ID."""
        ...
    
    def get_by_fingerprint(
        self,
        mailbox_id: str,
        fingerprint: str,
    ) -> EmailRecord | None:
        """Get record by fingerprint hash."""
        ...


@runtime_checkable
class CleanupRepositoryProtocol(Protocol):
    """Protocol for cleanup operations."""
    
    def list_cleanup_pending(
        self,
        mailbox_id: str,
        source_folder: str,
    ) -> list[EmailRecord]:
        """List records with cleanup_pending status."""
        ...
    
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
        """Mark record as cleanup_pending (message copied but not deleted)."""
        ...
    
    def mark_cleanup_completed(self, record_id: int) -> None:
        """Mark cleanup as completed (source message deleted)."""
        ...
    
    def acquire_cleanup_lock(
        self,
        record_id: int,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        """Try to acquire lock for cleanup operation."""
        ...


@runtime_checkable
class WorkerLockRepositoryProtocol(Protocol):
    """Protocol for global worker lock management."""
    
    def acquire_worker_lock(
        self,
        worker_id: str,
        lease_seconds: int,
    ) -> WorkerLockResult:
        """Acquire global worker lock."""
        ...
    
    def release_worker_lock(self, worker_id: str) -> None:
        """Release global worker lock."""
        ...
    
    def refresh_worker_lock(self, worker_id: str, lease_seconds: int) -> bool:
        """Refresh worker lock to prevent expiration."""
        ...
