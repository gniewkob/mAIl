"""Tests for async LLM gateway."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from mail_ai_agent.async_llm_gateway import AsyncLLMGateway
from mail_ai_agent.circuit_breaker import CircuitBreakerOpenError
from mail_ai_agent.config import MailboxConfig, Settings
from mail_ai_agent.schemas import LLMClassification, LLMEntities, ParsedEmail


class TestAsyncLLMGateway:
    """Test suite for AsyncLLMGateway."""
    
    @pytest.fixture
    def settings(self) -> Settings:
        """Create test settings."""
        return Settings(
            IMAP_HOST="test.example.com",
            IMAP_USER="test@example.com",
            IMAP_PASS="password",
            DRY_RUN=True,
            STATE_DB_PATH="/tmp/test.sqlite",
            AUDIT_LOG_PATH="/tmp/test.jsonl",
            DRAFT_DIR="/tmp/drafts",
            WORKER_ID="test-worker",
        )
    
    @pytest.fixture
    def mailbox(self) -> MailboxConfig:
        """Create test mailbox."""
        return MailboxConfig(
            mailbox_id="test_mailbox",
            imap_user="test@example.com",
            imap_pass="password",
            imap_host="test.example.com",
            imap_source_folder="INBOX",
            imap_target_folders={"question": "INBOX.Questions"},
            imap_uncertain_folder="INBOX.Uncertain",
            rules=[],
        )
    
    @pytest.fixture
    def mock_llm(self) -> MagicMock:
        """Create mock sync LLM gateway."""
        llm = MagicMock()
        llm.classify.return_value = LLMClassification(
            category="question",
            priority="medium",
            requires_reply=True,
            confidence=0.9,
            summary="Test classification",
            entities=LLMEntities(),
            draft_reply="Test reply",
            reasoning_short="Test reasoning",
        )
        return llm
    
    @pytest.fixture
    def parsed_email(self) -> ParsedEmail:
        """Create test parsed email."""
        return ParsedEmail(
            message_id="<test@example.com>",
            sender="customer@example.com",
            reply_to=None,
            to="test@example.com",
            date=None,
            subject="Test Subject",
            plain_text_body="Test content",
            html_body=None,
            normalized_body="Test content",
            attachment_metadata=[],
        )
    
    @pytest.mark.asyncio
    async def test_single_classify(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        mock_llm: MagicMock,
        parsed_email: ParsedEmail,
    ) -> None:
        """Test single async classification."""
        gateway = AsyncLLMGateway(settings, mock_llm, max_concurrent=3)
        
        result = await gateway.classify(parsed_email, mailbox)
        
        assert isinstance(result, LLMClassification)
        assert result.category == "question"
        assert result.confidence == 0.9
        mock_llm.classify.assert_called_once_with(parsed_email, mailbox)
    
    @pytest.mark.asyncio
    async def test_batch_classify(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        mock_llm: MagicMock,
    ) -> None:
        """Test batch classification."""
        gateway = AsyncLLMGateway(settings, mock_llm, max_concurrent=3)
        
        # Create multiple test emails
        emails = [
            ParsedEmail(
                message_id=f"<test{i}@example.com>",
                sender=f"customer{i}@example.com",
                reply_to=None,
                to="test@example.com",
                date=None,
                subject=f"Subject {i}",
                plain_text_body=f"Content {i}",
                html_body=None,
                normalized_body=f"Content {i}",
                attachment_metadata=[],
            )
            for i in range(5)
        ]
        
        items = [(email, mailbox) for email in emails]
        
        results = await gateway.classify_batch(items, max_concurrent=3)
        
        assert len(results) == 5
        for result in results:
            assert isinstance(result, LLMClassification)
            assert result.category == "question"
        
        # Should have been called 5 times
        assert mock_llm.classify.call_count == 5
    
    @pytest.mark.asyncio
    async def test_classify_with_timeout_success(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        mock_llm: MagicMock,
        parsed_email: ParsedEmail,
    ) -> None:
        """Test classification with timeout (success case)."""
        gateway = AsyncLLMGateway(settings, mock_llm)
        
        result = await gateway.classify_with_timeout(
            parsed_email, mailbox, timeout_seconds=5.0
        )
        
        assert isinstance(result, LLMClassification)
    
    @pytest.mark.asyncio
    async def test_classify_with_timeout_failure(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        parsed_email: ParsedEmail,
    ) -> None:
        """Test classification with timeout (timeout case)."""
        # Create slow LLM
        slow_llm = MagicMock()
        
        def slow_classify(*args, **kwargs):
            import time
            time.sleep(0.5)  # Simulate slow call
            return LLMClassification(
                category="question",
                priority="medium",
                requires_reply=False,
                confidence=0.5,
                summary="Slow",
                entities=LLMEntities(),
                draft_reply=None,
                reasoning_short="Slow",
            )
        
        slow_llm.classify.side_effect = slow_classify
        
        gateway = AsyncLLMGateway(settings, slow_llm)
        
        with pytest.raises(asyncio.TimeoutError):
            await gateway.classify_with_timeout(
                parsed_email, mailbox, timeout_seconds=0.1
            )
    
    @pytest.mark.asyncio
    async def test_concurrency_limit(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        parsed_email: ParsedEmail,
    ) -> None:
        """Test that concurrency limit is respected."""
        max_concurrent = 2
        
        # Track concurrent calls
        active_calls = 0
        max_active = 0
        
        def tracking_classify(*args, **kwargs):
            nonlocal active_calls, max_active
            active_calls += 1
            max_active = max(max_active, active_calls)
            
            import time
            time.sleep(0.05)  # Small delay
            
            active_calls -= 1
            return LLMClassification(
                category="question",
                priority="medium",
                requires_reply=False,
                confidence=0.5,
                summary="Test",
                entities=LLMEntities(),
                draft_reply=None,
                reasoning_short="Test",
            )
        
        tracking_llm = MagicMock()
        tracking_llm.classify.side_effect = tracking_classify
        
        gateway = AsyncLLMGateway(settings, tracking_llm, max_concurrent=max_concurrent)
        
        # Create 5 parallel calls
        emails = [
            parsed_email.model_copy(update={"message_id": f"<test{i}@example.com>"})
            for i in range(5)
        ]
        
        await asyncio.gather(*[
            gateway.classify(email, mailbox)
            for email in emails
        ])
        
        # Should never exceed max_concurrent
        assert max_active <= max_concurrent, f"Max concurrent was {max_active}, expected <= {max_concurrent}"
    
    @pytest.mark.asyncio
    async def test_batch_with_exceptions(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        parsed_email: ParsedEmail,
    ) -> None:
        """Test batch classification when some calls fail."""
        failing_llm = MagicMock()
        
        call_count = 0
        def mixed_classify(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise ValueError(f"Simulated failure for call {call_count}")
            return LLMClassification(
                category="question",
                priority="medium",
                requires_reply=False,
                confidence=0.8,
                summary="Success",
                entities=LLMEntities(),
                draft_reply=None,
                reasoning_short="Success",
            )
        
        failing_llm.classify.side_effect = mixed_classify
        
        gateway = AsyncLLMGateway(settings, failing_llm)
        
        emails = [
            parsed_email.model_copy(update={"message_id": f"<test{i}@example.com>"})
            for i in range(4)
        ]
        items = [(email, mailbox) for email in emails]
        
        results = await gateway.classify_batch(items)
        
        # Should have 4 results
        assert len(results) == 4
        
        # Check mix of successes and failures
        successes = [r for r in results if isinstance(r, LLMClassification)]
        failures = [r for r in results if isinstance(r, Exception)]
        
        assert len(successes) == 2  # Calls 1 and 3
        assert len(failures) == 2   # Calls 2 and 4


class TestAsyncPerformance:
    """Performance tests for async operations."""
    
    @pytest.mark.asyncio
    async def test_parallel_vs_sequential(self) -> None:
        """Compare parallel vs sequential processing time."""
        import time
        
        settings = Settings(
            IMAP_HOST="test.example.com",
            IMAP_USER="test@example.com",
            IMAP_PASS="password",
            DRY_RUN=True,
            STATE_DB_PATH="/tmp/test.sqlite",
            AUDIT_LOG_PATH="/tmp/test.jsonl",
            DRAFT_DIR="/tmp/drafts",
            WORKER_ID="test-worker",
        )
        
        mailbox = MailboxConfig(
            mailbox_id="test",
            imap_user="test@example.com",
            imap_pass="password",
            imap_host="test.example.com",
            imap_source_folder="INBOX",
            imap_target_folders={},
            imap_uncertain_folder="INBOX.Uncertain",
            rules=[],
        )
        
        # Create mock LLM with delay
        delay_llm = MagicMock()
        
        def delayed_classify(*args, **kwargs):
            time.sleep(0.05)  # 50ms delay
            return LLMClassification(
                category="question",
                priority="medium",
                requires_reply=False,
                confidence=0.5,
                summary="Test",
                entities=LLMEntities(),
                draft_reply=None,
                reasoning_short="Test",
            )
        
        delay_llm.classify.side_effect = delayed_classify
        
        # Create test emails
        emails = [
            ParsedEmail(
                message_id=f"<test{i}@example.com>",
                sender=f"user{i}@example.com",
                reply_to=None,
                to="test@example.com",
                date=None,
                subject=f"Subject {i}",
                plain_text_body=f"Content {i}",
                html_body=None,
                normalized_body=f"Content {i}",
                attachment_metadata=[],
            )
            for i in range(10)
        ]
        
        # Sequential processing
        start = time.perf_counter()
        for email in emails:
            delay_llm.classify(email, mailbox)
        sequential_time = time.perf_counter() - start
        
        # Reset mock
        delay_llm.reset_mock()
        delay_llm.classify.side_effect = delayed_classify
        
        # Parallel processing with async
        gateway = AsyncLLMGateway(settings, delay_llm, max_concurrent=5)
        
        start = time.perf_counter()
        await gateway.classify_batch([(e, mailbox) for e in emails])
        parallel_time = time.perf_counter() - start
        
        # Parallel should be significantly faster
        speedup = sequential_time / parallel_time
        print(f"\nSequential: {sequential_time:.3f}s")
        print(f"Parallel: {parallel_time:.3f}s")
        print(f"Speedup: {speedup:.1f}x")
        
        # With 5 concurrent workers and 10 items taking 50ms each:
        # Sequential: 10 * 50ms = 500ms
        # Parallel (5 concurrent): 2 batches * 50ms = 100ms
        # Expected speedup: ~5x
        assert speedup > 2.0, f"Expected significant speedup, got {speedup:.1f}x"
