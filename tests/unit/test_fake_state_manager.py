"""Tests for FakeStateManager - in-memory implementation for fast tests."""

from __future__ import annotations

import pytest

from mail_ai_agent import FakeStateManager
from mail_ai_agent.constants import WorkflowStatus
from mail_ai_agent.schemas import LeaseAcquireResult


class TestFakeStateManager:
    """Test suite for FakeStateManager."""
    
    @pytest.fixture
    def state(self) -> FakeStateManager:
        """Create fresh FakeStateManager for each test."""
        return FakeStateManager()
    
    def test_acquire_lease_new_message(self, state: FakeStateManager) -> None:
        """Should acquire lease for new message."""
        result = state.acquire_lease(
            mailbox_id="test_mailbox",
            message_id="<msg-1@example.com>",
            fingerprint="abc123",
            content_fingerprint=None,
            imap_uid="123",
            uidvalidity="999",
            sender="sender@example.com",
            sender_sha256=None,
            subject="Test Subject",
            subject_sha256=None,
            source_folder="INBOX",
            internaldate=None,
            worker_id="worker-1",
            lease_seconds=60,
            max_retries=3,
        )
        
        assert result.outcome == "acquired"
        assert result.record is not None
        assert result.record.status == WorkflowStatus.PROCESSING
        assert result.record.lock_owner == "worker-1"
    
    def test_acquire_lease_already_done(self, state: FakeStateManager) -> None:
        """Should return already_done for processed message."""
        # First acquire and process
        result1 = state.acquire_lease(
            mailbox_id="test_mailbox",
            message_id="<msg-1@example.com>",
            fingerprint="abc123",
            content_fingerprint=None,
            imap_uid="123",
            uidvalidity="999",
            sender="sender@example.com",
            sender_sha256=None,
            subject="Test Subject",
            subject_sha256=None,
            source_folder="INBOX",
            internaldate=None,
            worker_id="worker-1",
            lease_seconds=60,
            max_retries=3,
        )
        
        # Mark as processed
        state.mark_processed(
            result1.record.id,
            category="question",
            confidence=0.9,
            target_folder="INBOX.Processed",
            target_uid="456",
            action_taken="route_reply",
            draft_path=None,
            rule_hit=None,
            model_name=None,
            model_latency_ms=None,
        )
        
        # Try to acquire again
        result2 = state.acquire_lease(
            mailbox_id="test_mailbox",
            message_id="<msg-1@example.com>",
            fingerprint="abc123",
            content_fingerprint=None,
            imap_uid="123",
            uidvalidity="999",
            sender="sender@example.com",
            sender_sha256=None,
            subject="Test Subject",
            subject_sha256=None,
            source_folder="INBOX",
            internaldate=None,
            worker_id="worker-2",
            lease_seconds=60,
            max_retries=3,
        )
        
        assert result2.outcome == "already_done"
        assert "already processed" in result2.reason
    
    def test_worker_lock_acquire_and_release(self, state: FakeStateManager) -> None:
        """Should acquire and release worker lock."""
        # Acquire lock
        result1 = state.acquire_worker_lock("worker-1", lease_seconds=60)
        assert result1.acquired is True
        
        # Try to acquire by different worker
        result2 = state.acquire_worker_lock("worker-2", lease_seconds=60)
        assert result2.acquired is False
        assert result2.lock_owner == "worker-1"
        
        # Release and reacquire
        state.release_worker_lock("worker-1")
        result3 = state.acquire_worker_lock("worker-2", lease_seconds=60)
        assert result3.acquired is True
    
    def test_cleanup_candidates(self, state: FakeStateManager) -> None:
        """Should list cleanup candidates."""
        # Create record via lease
        result = state.acquire_lease(
            mailbox_id="test_mailbox",
            message_id="<msg-1@example.com>",
            fingerprint="abc123",
            content_fingerprint=None,
            imap_uid="123",
            uidvalidity="999",
            sender="sender@example.com",
            sender_sha256=None,
            subject="Test Subject",
            subject_sha256=None,
            source_folder="INBOX",
            internaldate=None,
            worker_id="worker-1",
            lease_seconds=60,
            max_retries=3,
        )
        
        # Mark as cleanup pending
        state.mark_move_cleanup_pending(
            result.record.id,
            category="question",
            confidence=0.9,
            target_folder="INBOX.Processed",
            target_uid="456",
            draft_path=None,
            rule_hit=None,
            model_name=None,
            model_latency_ms=None,
            error_message="Delete failed",
            error_type="RuntimeError",
        )
        
        # List candidates
        candidates = state.list_cleanup_candidates("test_mailbox", "INBOX")
        assert len(candidates) == 1
        assert candidates[0].status == WorkflowStatus.CLEANUP_PENDING
        
        # Complete cleanup
        state.mark_cleanup_completed(result.record.id)
        
        candidates = state.list_cleanup_candidates("test_mailbox", "INBOX")
        assert len(candidates) == 0
    
    def test_get_by_methods(self, state: FakeStateManager) -> None:
        """Test retrieval methods."""
        # Create record
        result = state.acquire_lease(
            mailbox_id="test_mailbox",
            message_id="<msg-1@example.com>",
            fingerprint="abc123",
            content_fingerprint=None,
            imap_uid="123",
            uidvalidity="999",
            sender="sender@example.com",
            sender_sha256=None,
            subject="Test Subject",
            subject_sha256=None,
            source_folder="INBOX",
            internaldate=None,
            worker_id="worker-1",
            lease_seconds=60,
            max_retries=3,
        )
        
        # Get by ID
        by_id = state.get_by_id(result.record.id)
        assert by_id is not None
        assert by_id.fingerprint == "abc123"
        
        # Get by message ID
        by_msg_id = state.get_by_message_id("test_mailbox", "<msg-1@example.com>")
        assert by_msg_id is not None
        assert by_msg_id.fingerprint == "abc123"
        
        # Get by fingerprint
        by_fp = state.get_by_fingerprint("test_mailbox", "abc123")
        assert by_fp is not None
        assert by_fp.message_id == "<msg-1@example.com>"
    
    def test_mark_uncertain_and_failed(self, state: FakeStateManager) -> None:
        """Test marking as uncertain and failed."""
        # Create record
        result = state.acquire_lease(
            mailbox_id="test_mailbox",
            message_id="<msg-1@example.com>",
            fingerprint="abc123",
            content_fingerprint=None,
            imap_uid="123",
            uidvalidity="999",
            sender="sender@example.com",
            sender_sha256=None,
            subject="Test Subject",
            subject_sha256=None,
            source_folder="INBOX",
            internaldate=None,
            worker_id="worker-1",
            lease_seconds=60,
            max_retries=3,
        )
        
        # Mark as uncertain
        state.mark_uncertain(
            result.record.id,
            category="other",
            confidence=0.0,
            target_folder="INBOX.Uncertain",
            target_uid="999",
            action_taken="route_uncertain",
            error_message="Low confidence",
        )
        
        record = state.get_by_id(result.record.id)
        assert record.status == WorkflowStatus.UNCERTAIN
        assert record.error_message == "Low confidence"
        
        # Mark as failed
        state.mark_failed(
            result.record.id,
            error_message="Processing failed",
            error_type="RuntimeError",
        )
        
        record = state.get_by_id(result.record.id)
        assert record.status == WorkflowStatus.FAILED
        assert record.last_error_type == "RuntimeError"


class TestFakeStateManagerPerformance:
    """Performance comparison tests."""
    
    def test_many_operations_performance(self) -> None:
        """FakeStateManager should be fast for many operations."""
        import time
        
        state = FakeStateManager()
        start = time.perf_counter()
        
        # Perform many operations
        for i in range(100):
            result = state.acquire_lease(
                mailbox_id="test_mailbox",
                message_id=f"<msg-{i}@example.com>",
                fingerprint=f"fp{i}",
                content_fingerprint=None,
                imap_uid=str(i),
                uidvalidity="999",
                sender="sender@example.com",
                sender_sha256=None,
                subject=f"Subject {i}",
                subject_sha256=None,
                source_folder="INBOX",
                internaldate=None,
                worker_id="worker-1",
                lease_seconds=60,
                max_retries=3,
            )
            
            state.mark_processed(
                result.record.id,
                category="question",
                confidence=0.9,
                target_folder="INBOX.Processed",
                target_uid=str(i + 1000),
                action_taken="route_reply",
                draft_path=None,
                rule_hit=None,
                model_name=None,
                model_latency_ms=None,
            )
        
        elapsed = time.perf_counter() - start
        
        # Should complete 100 operations in less than 100ms (much faster than SQLite)
        assert elapsed < 0.1, f"Too slow: {elapsed:.3f}s"
