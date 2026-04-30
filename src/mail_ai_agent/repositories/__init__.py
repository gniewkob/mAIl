"""Repository layer for state management.

This module provides repository pattern abstraction over SQLite storage,
enabling testability and separation of concerns.
"""

from .base import LeaseRepositoryProtocol, StateRepositoryProtocol, CleanupRepositoryProtocol, WorkerLockRepositoryProtocol
from .fake_repositories import FakeLeaseRepository, FakeStateManager, FakeStateRepository, FakeCleanupRepository, FakeWorkerLockRepository
from .sqlite_repositories import SqliteLeaseRepository, SqliteStateRepository, SqliteCleanupRepository, SqliteWorkerLockRepository

__all__ = [
    # Protocols
    "LeaseRepositoryProtocol",
    "StateRepositoryProtocol",
    "CleanupRepositoryProtocol",
    "WorkerLockRepositoryProtocol",
    # SQLite implementations
    "SqliteLeaseRepository",
    "SqliteStateRepository",
    "SqliteCleanupRepository",
    "SqliteWorkerLockRepository",
    # Fake implementations
    "FakeLeaseRepository",
    "FakeStateRepository",
    "FakeCleanupRepository",
    "FakeWorkerLockRepository",
    "FakeStateManager",
]
