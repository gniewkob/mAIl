"""Tests for thread-safety of FakeRepositories."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from mail_ai_agent.constants import WorkflowStatus
from mail_ai_agent.repositories.fake_repositories import FakeStateManager


class TestFakeStateManagerThreadSafety:
    """Test thread-safety of FakeStateManager."""

    def test_concurrent_lease_acquisitions_different_messages(self):
        """Multiple threads can acquire leases for different messages concurrently."""
        state_mgr = FakeStateManager()
        results = []
        errors = []

        def acquire_lease(i):
            try:
                result = state_mgr.acquire_lease(
                    mailbox_id="test@example.com",
                    fingerprint=f"fp-{i}",
                    message_id=f"msg-{i}",
                    content_fingerprint=f"cfp-{i}",
                    imap_uid=str(i),
                    uidvalidity="123",
                    sender="test@example.com",
                    sender_sha256=None,
                    subject="Test",
                    subject_sha256=None,
                    source_folder="INBOX",
                    internaldate=None,
                    worker_id="worker-1",
                    lease_seconds=300,
                    max_retries=3,
                )
                return result.outcome
            except Exception as e:
                errors.append(str(e))
                raise

        # Use thread pool for concurrent execution
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(acquire_lease, i) for i in range(50)]
            for future in as_completed(futures):
                results.append(future.result())

        assert not errors, f"Errors during concurrent acquisitions: {errors}"
        assert len(results) == 50
        # All should be acquired (different fingerprints)
        assert all(r == "acquired" for r in results)
        assert len(state_mgr._leases._records) == 50

    def test_concurrent_lease_acquisitions_same_message_race_condition(self):
        """Only one thread should acquire lease for the same message."""
        state_mgr = FakeStateManager()
        results = []
        errors = []

        def acquire_lease(i):
            try:
                result = state_mgr.acquire_lease(
                    mailbox_id="test@example.com",
                    fingerprint="same-fingerprint",  # Same for all
                    message_id="msg-1",
                    content_fingerprint="cfp-1",
                    imap_uid="1",
                    uidvalidity="123",
                    sender="test@example.com",
                    sender_sha256=None,
                    subject="Test",
                    subject_sha256=None,
                    source_folder="INBOX",
                    internaldate=None,
                    worker_id=f"worker-{i}",
                    lease_seconds=300,
                    max_retries=3,
                )
                return result.outcome
            except Exception as e:
                errors.append(str(e))
                raise

        # Launch many threads trying to acquire the same lease
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(acquire_lease, i) for i in range(20)]
            for future in as_completed(futures):
                results.append(future.result())

        assert not errors
        # Exactly one should acquire
        acquired_count = results.count("acquired")
        locked_count = results.count("locked")

        assert acquired_count == 1, f"Expected 1 acquired, got {acquired_count}"
        assert locked_count == 19, f"Expected 19 locked, got {locked_count}"

    def test_state_transitions_are_thread_safe(self):
        """State transitions from multiple threads are safe."""
        state_mgr = FakeStateManager()

        # First acquire a lease
        result = state_mgr.acquire_lease(
            mailbox_id="test@example.com",
            fingerprint="fp-1",
            message_id="msg-1",
            content_fingerprint="cfp-1",
            imap_uid="1",
            uidvalidity="123",
            sender="test@example.com",
            sender_sha256=None,
            subject="Test",
            subject_sha256=None,
            source_folder="INBOX",
            internaldate=None,
            worker_id="worker-1",
            lease_seconds=300,
            max_retries=3,
        )
        record_id = result.record.id

        errors = []

        def mark_processed(i):
            try:
                state_mgr.mark_processed(
                    record_id=record_id,
                    category="test",
                    confidence=0.9,
                    target_folder="Archive",
                    target_uid=str(i),
                    action_taken="move",
                    draft_path=None,
                    rule_hit=None,
                    model_name="test-model",
                    model_latency_ms=100,
                )
            except Exception as e:
                errors.append(str(e))

        # Multiple threads trying to mark as processed
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(mark_processed, i) for i in range(10)]
            for future in as_completed(futures):
                future.result()

        # Should complete without errors (last write wins)
        assert not errors
        record = state_mgr.get_by_id(record_id)
        assert record.status == WorkflowStatus.PROCESSED

    def test_worker_lock_operations_are_thread_safe(self):
        """Worker lock operations from multiple threads are safe."""
        state_mgr = FakeStateManager()
        results = []

        def try_acquire_lock(i):
            result = state_mgr.acquire_worker_lock(
                worker_id=f"worker-{i}",
                lease_seconds=60,
            )
            return result.acquired

        # Multiple threads trying to acquire the same lock
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(try_acquire_lock, i) for i in range(10)]
            for future in as_completed(futures):
                results.append(future.result())

        # Exactly one should acquire the lock
        assert results.count(True) == 1
        assert results.count(False) == 9

    def test_nested_lock_acquisition_does_not_deadlock(self):
        """Nested lock acquisition (same thread) should not deadlock."""
        state_mgr = FakeStateManager()

        # This should not hang due to RLock
        result1 = state_mgr.acquire_lease(
            mailbox_id="test@example.com",
            fingerprint="fp-1",
            message_id="msg-1",
            content_fingerprint="cfp-1",
            imap_uid="1",
            uidvalidity="123",
            sender="test@example.com",
            sender_sha256=None,
            subject="Test",
            subject_sha256=None,
            source_folder="INBOX",
            internaldate=None,
            worker_id="worker-1",
            lease_seconds=300,
            max_retries=3,
        )

        # Same thread acquiring again (expired lease)
        time.sleep(0.01)  # Small delay to ensure time change
        result2 = state_mgr.acquire_lease(
            mailbox_id="test@example.com",
            fingerprint="fp-1",
            message_id="msg-1",
            content_fingerprint="cfp-1",
            imap_uid="1",
            uidvalidity="123",
            sender="test@example.com",
            sender_sha256=None,
            subject="Test",
            subject_sha256=None,
            source_folder="INBOX",
            internaldate=None,
            worker_id="worker-1",
            lease_seconds=300,
            max_retries=3,
        )

        assert result1.outcome == "acquired"
        # Second acquisition should succeed (same worker, lease extended)
        assert result2.outcome in ("acquired", "locked")
