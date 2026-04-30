"""Base class for StateManager implementations.

This module provides a base class with common proxy methods to repositories,
eliminating duplication between SQLite and Fake implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .constants import MOVE_CLEANUP_PENDING_ACTION
from .schemas import EmailRecord, LeaseAcquireResult, WorkerLockResult

if TYPE_CHECKING:
    from .repositories.base import (
        CleanupRepositoryProtocol,
        LeaseRepositoryProtocol,
        StateRepositoryProtocol,
        WorkerLockRepositoryProtocol,
    )


class BaseStateManager:
    """Base state manager with common repository proxy methods.
    
    Subclasses must initialize:
    - _leases: LeaseRepositoryProtocol
    - _state: StateRepositoryProtocol
    - _cleanup: CleanupRepositoryProtocol
    - _worker_locks: WorkerLockRepositoryProtocol
    """
    
    # To be set by subclasses
    _leases: LeaseRepositoryProtocol
    _state: StateRepositoryProtocol
    _cleanup: CleanupRepositoryProtocol
    _worker_locks: WorkerLockRepositoryProtocol
    
    # Lease operations
    def acquire_lease(self, **kwargs) -> LeaseAcquireResult:
        """Acquire processing lease for a message."""
        return self._leases.acquire_lease(**kwargs)
    
    def is_message_processed(self, mailbox_id: str, fingerprint: str) -> EmailRecord | None:
        """Check if message was already processed."""
        return self._leases.is_message_processed(mailbox_id, fingerprint)
    
    # State operations
    def mark_processed(self, record_id: int, **kwargs) -> None:
        """Mark record as successfully processed."""
        self._state.mark_processed(record_id, **kwargs)
    
    def mark_uncertain(self, record_id: int, **kwargs) -> None:
        """Mark record as uncertain."""
        self._state.mark_uncertain(record_id, **kwargs)
    
    def mark_failed(self, record_id: int, **kwargs) -> None:
        """Mark record as failed."""
        self._state.mark_failed(record_id, **kwargs)
    
    def get_by_id(self, record_id: int) -> EmailRecord | None:
        """Get record by ID."""
        return self._state.get_by_id(record_id)
    
    def get_by_message_id(self, mailbox_id: str, message_id: str) -> EmailRecord | None:
        """Get record by message ID."""
        return self._state.get_by_message_id(mailbox_id, message_id)
    
    def get_by_fingerprint(self, mailbox_id: str, fingerprint: str) -> EmailRecord | None:
        """Get record by fingerprint."""
        return self._state.get_by_fingerprint(mailbox_id, fingerprint)
    
    # Cleanup operations
    def list_cleanup_candidates(self, mailbox_id: str, source_folder: str) -> list[EmailRecord]:
        """List records with cleanup_pending status."""
        return self._cleanup.list_cleanup_pending(mailbox_id, source_folder)
    
    def mark_move_cleanup_pending(self, record_id: int, **kwargs) -> None:
        """Mark record as cleanup_pending."""
        self._cleanup.mark_move_cleanup_pending(record_id, **kwargs)
    
    def mark_cleanup_completed(self, record_id: int) -> None:
        """Mark cleanup as completed."""
        self._cleanup.mark_cleanup_completed(record_id)
    
    def acquire_cleanup_lock(self, record_id: int, worker_id: str, lease_seconds: int) -> bool:
        """Acquire lock for cleanup operation."""
        return self._cleanup.acquire_cleanup_lock(record_id, worker_id, lease_seconds)
    
    # Worker lock operations
    def acquire_worker_lock(self, worker_id: str, lease_seconds: int) -> WorkerLockResult:
        """Acquire global worker lock."""
        return self._worker_locks.acquire_worker_lock(worker_id, lease_seconds)
    
    def release_worker_lock(self, worker_id: str) -> None:
        """Release global worker lock."""
        self._worker_locks.release_worker_lock(worker_id)
    
    def refresh_worker_lock(self, worker_id: str, lease_seconds: int) -> bool:
        """Refresh worker lock."""
        return self._worker_locks.refresh_worker_lock(worker_id, lease_seconds)
