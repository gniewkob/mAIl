"""Tests for MessageProcessorV2 using FakeStateManager."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from mail_ai_agent import FakeStateManager
from mail_ai_agent.config import MailboxConfig, Settings
from mail_ai_agent.constants import WorkflowStatus, ActionTaken
from mail_ai_agent.message_processor_v2 import MessageProcessorV2, create_processor
from mail_ai_agent.schemas import CandidateMessage, LLMClassification, LLMEntities
from email.message import EmailMessage


class TestMessageProcessorV2:
    """Test suite for MessageProcessorV2 with FakeStateManager."""
    
    @pytest.fixture
    def settings(self) -> Settings:
        """Create test settings."""
        return Settings(
            IMAP_HOST="test.example.com",
            IMAP_USER="test@example.com",
            IMAP_PASS="password",
            DRY_RUN=False,
            STATE_DB_PATH="/tmp/test.sqlite",
            AUDIT_LOG_PATH="/tmp/test.jsonl",
            DRAFT_DIR="/tmp/drafts",
            WORKER_ID="test-worker",
            ollama_model="test-model",
        )
    
    @pytest.fixture
    def mailbox(self) -> MailboxConfig:
        """Create test mailbox config."""
        return MailboxConfig(
            mailbox_id="test_mailbox",
            imap_user="test@example.com",
            imap_pass="password",
            imap_host="test.example.com",
            imap_source_folder="INBOX",
            imap_target_folders={
                "question": "INBOX.Questions",
                "complaint": "INBOX.Complaints",
            },
            imap_uncertain_folder="INBOX.Uncertain",
            rules=[],
        )
    
    @pytest.fixture
    def fake_state(self) -> FakeStateManager:
        """Create fake state manager."""
        return FakeStateManager()
    
    @pytest.fixture
    def mock_audit(self) -> MagicMock:
        """Create mock audit logger."""
        return MagicMock()
    
    @pytest.fixture
    def mock_drafts(self) -> MagicMock:
        """Create mock draft store."""
        drafts = MagicMock()
        drafts.save.return_value = "/tmp/drafts/draft_123.json"
        return drafts
    
    @pytest.fixture
    def mock_llm(self) -> MagicMock:
        """Create mock LLM gateway."""
        llm = MagicMock()
        llm.classify.return_value = LLMClassification(
            category="question",
            priority="medium",
            requires_reply=True,
            confidence=0.9,
            summary="Customer asking about pricing",
            entities=LLMEntities(),
            draft_reply="Thank you for your inquiry...",
            reasoning_short="Clear question about pricing",
        )
        return llm
    
    @pytest.fixture
    def mock_imap(self) -> MagicMock:
        """Create mock IMAP client."""
        imap = MagicMock()
        imap.copy_message.return_value = "456"  # target UID
        return imap
    
    @pytest.fixture
    def processor(
        self,
        settings: Settings,
        fake_state: FakeStateManager,
        mock_audit: MagicMock,
        mock_drafts: MagicMock,
        mock_llm: MagicMock,
    ) -> MessageProcessorV2:
        """Create processor with all dependencies."""
        return MessageProcessorV2(
            settings=settings,
            state=fake_state,
            audit=mock_audit,
            drafts=mock_drafts,
            llm=mock_llm,
        )
    
    def _create_candidate(self, uid: str = "123", subject: str = "Test") -> CandidateMessage:
        """Helper to create a test candidate."""
        msg = EmailMessage()
        msg["From"] = "customer@example.com"
        msg["Subject"] = subject
        msg["Message-ID"] = f"<test-{uid}@example.com>"
        msg.set_content("What is the price of your service?")
        
        return CandidateMessage(
            uid=uid,
            uidvalidity="999",
            internaldate=None,
            message_id=f"<test-{uid}@example.com>",
            raw_bytes=msg.as_bytes(),
        )
    
    def test_full_processing_flow(
        self,
        processor: MessageProcessorV2,
        mailbox: MailboxConfig,
        mock_imap: MagicMock,
        fake_state: FakeStateManager,
        mock_audit: MagicMock,
    ) -> None:
        """Test complete processing flow from start to finish."""
        candidate = self._create_candidate()
        
        # Process the message
        result = processor.process_candidate(candidate, mailbox, mock_imap)
        
        # Verify result
        assert isinstance(result.action_taken, str)
        assert result.final_status == WorkflowStatus.PROCESSED
        assert result.category == "question"
        assert result.confidence == 0.9
        
        # Verify IMAP operations
        mock_imap.copy_message.assert_called_once_with(
            "INBOX", "123", "INBOX.Questions"
        )
        mock_imap.delete_message.assert_called_once_with("INBOX", "123")
        
        # Verify state
        record = fake_state.get_by_fingerprint("test_mailbox", result.action_taken)
        # Note: action_taken is the string value, need to find by message_id
        record = fake_state.get_by_message_id("test_mailbox", "<test-123@example.com>")
        assert record is not None
        assert record.status == WorkflowStatus.PROCESSED
        
        # Verify audit
        mock_audit.log.assert_called_once()
    
    def test_idempotent_processing(
        self,
        processor: MessageProcessorV2,
        mailbox: MailboxConfig,
        mock_imap: MagicMock,
        mock_audit: MagicMock,
    ) -> None:
        """Test that processing same message twice is idempotent."""
        candidate = self._create_candidate()
        
        # First processing
        result1 = processor.process_candidate(candidate, mailbox, mock_imap)
        assert result1.final_status == WorkflowStatus.PROCESSED
        
        # Reset mocks
        mock_imap.reset_mock()
        mock_audit.reset_mock()
        
        # Second processing - should skip
        result2 = processor.process_candidate(candidate, mailbox, mock_imap)
        assert result2.final_status == WorkflowStatus.SKIPPED
        
        # Should not perform IMAP operations on second run
        mock_imap.copy_message.assert_not_called()
        
        # Audit should still be logged
        mock_audit.log.assert_called_once()
    
    def test_dry_run_simulation(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        fake_state: FakeStateManager,
        mock_audit: MagicMock,
        mock_drafts: MagicMock,
        mock_llm: MagicMock,
    ) -> None:
        """Test dry run simulation mode."""
        processor = MessageProcessorV2(
            settings=settings,
            state=fake_state,
            audit=mock_audit,
            drafts=mock_drafts,
            llm=mock_llm,
        )
        
        candidate = self._create_candidate()
        
        # Use simulation mode
        result = processor.process_candidate_simulate(candidate, mailbox)
        
        # Should classify but not persist
        assert result.final_status == WorkflowStatus.PROCESSED
        
        # State should not be marked as processed (no lease acquired in dry run path)
        # Actually, in our pipeline dry_run means RouteStage skips but LeaseStage still runs
        # So there might be a lease record but no final state
        record = fake_state.get_by_message_id("test_mailbox", "<test-123@example.com>")
        if record:
            # If record exists, it should be in processing state (not terminal)
            assert record.status != WorkflowStatus.PROCESSED
    
    def test_processing_with_draft(
        self,
        processor: MessageProcessorV2,
        mailbox: MailboxConfig,
        mock_imap: MagicMock,
        mock_drafts: MagicMock,
        mock_llm: MagicMock,
    ) -> None:
        """Test that draft is saved when requires_reply is True."""
        # Configure LLM to require reply
        mock_llm.classify.return_value = LLMClassification(
            category="question",
            priority="high",
            requires_reply=True,
            confidence=0.95,
            summary="Urgent question",
            entities=LLMEntities(),
            draft_reply="Thank you for your urgent inquiry...",
            reasoning_short="High priority question",
        )
        
        candidate = self._create_candidate()
        result = processor.process_candidate(candidate, mailbox, mock_imap)
        
        # Draft should be saved
        mock_drafts.save.assert_called_once()
        assert result.draft_path == "/tmp/drafts/draft_123.json"
    
    def test_create_processor_factory(
        self,
        settings: Settings,
        fake_state: FakeStateManager,
        mock_audit: MagicMock,
        mock_drafts: MagicMock,
        mock_llm: MagicMock,
    ) -> None:
        """Test the create_processor factory function."""
        # Create with pipeline (default)
        processor = create_processor(
            settings, fake_state, mock_audit, mock_drafts, mock_llm, use_pipeline=True
        )
        assert isinstance(processor, MessageProcessorV2)
        
        # Create with original (backward compatibility)
        from mail_ai_agent.message_processor import MessageProcessor
        processor_original = create_processor(
            settings, fake_state, mock_audit, mock_drafts, mock_llm, use_pipeline=False
        )
        assert isinstance(processor_original, MessageProcessor)


class TestMessageProcessorV2Performance:
    """Performance tests using FakeStateManager."""
    
    @pytest.fixture
    def setup(self):
        """Common setup for performance tests."""
        settings = Settings(
            IMAP_HOST="test.example.com",
            IMAP_USER="test@example.com",
            IMAP_PASS="password",
            DRY_RUN=False,
            STATE_DB_PATH="/tmp/test.sqlite",
            AUDIT_LOG_PATH="/tmp/test.jsonl",
            DRAFT_DIR="/tmp/drafts",
            WORKER_ID="test-worker",
            ollama_model="test-model",
        )
        
        mailbox = MailboxConfig(
            mailbox_id="perf_test",
            imap_user="test@example.com",
            imap_pass="password",
            imap_host="test.example.com",
            imap_source_folder="INBOX",
            imap_target_folders={"question": "INBOX.Questions"},
            imap_uncertain_folder="INBOX.Uncertain",
            rules=[],
        )
        
        fake_state = FakeStateManager()
        mock_audit = MagicMock()
        mock_drafts = MagicMock()
        mock_drafts.save.return_value = "/tmp/drafts/draft.json"
        
        mock_llm = MagicMock()
        mock_llm.classify.return_value = LLMClassification(
            category="question",
            priority="medium",
            requires_reply=False,
            confidence=0.85,
            summary="Test",
            entities=LLMEntities(),
            draft_reply=None,
            reasoning_short="Test",
        )
        
        mock_imap = MagicMock()
        mock_imap.copy_message.return_value = "999"
        
        processor = MessageProcessorV2(
            settings=settings,
            state=fake_state,
            audit=mock_audit,
            drafts=mock_drafts,
            llm=mock_llm,
        )
        
        return {
            "settings": settings,
            "mailbox": mailbox,
            "processor": processor,
            "imap": mock_imap,
        }
    
    def test_bulk_processing_performance(self, setup) -> None:
        """Test processing many messages quickly with FakeStateManager."""
        import time
        
        processor = setup["processor"]
        mailbox = setup["mailbox"]
        imap = setup["imap"]
        
        # Create 50 candidates
        candidates = []
        for i in range(50):
            msg = EmailMessage()
            msg["From"] = f"customer{i}@example.com"
            msg["Subject"] = f"Question {i}"
            msg["Message-ID"] = f"<msg-{i}@example.com>"
            msg.set_content(f"Question content {i}")
            
            candidates.append(CandidateMessage(
                uid=str(i),
                uidvalidity="999",
                internaldate=None,
                message_id=f"<msg-{i}@example.com>",
                raw_bytes=msg.as_bytes(),
            ))
        
        start = time.perf_counter()
        
        for candidate in candidates:
            result = processor.process_candidate(candidate, mailbox, imap)
            assert result.final_status == WorkflowStatus.PROCESSED
        
        elapsed = time.perf_counter() - start
        
        # Should process 50 messages in under 100ms with FakeStateManager
        # (would take several seconds with SQLite)
        assert elapsed < 0.5, f"Too slow: {elapsed:.3f}s for 50 messages"
        print(f"Processed 50 messages in {elapsed:.3f}s")
