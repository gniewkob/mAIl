"""Tests for the processing pipeline."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from mail_ai_agent import FakeStateManager
from mail_ai_agent.config import MailboxConfig, Settings
from mail_ai_agent.constants import WorkflowStatus
from mail_ai_agent.pipeline import (
    AuditStage,
    ClassifyStage,
    LeaseStage,
    ParseStage,
    ProcessingPipeline,
    ProcessingResult,
    RouteStage,
)
from mail_ai_agent.schemas import CandidateMessage, LeaseAcquireResult, ParsedEmail


class TestProcessingPipeline:
    """Test suite for ProcessingPipeline."""
    
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
        """Create test mailbox config."""
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
    def candidate(self) -> CandidateMessage:
        """Create test candidate message."""
        from email.message import EmailMessage
        
        msg = EmailMessage()
        msg["From"] = "sender@example.com"
        msg["Subject"] = "Test Question"
        msg["Message-ID"] = "<test-123@example.com>"
        msg.set_content("What is the price of your service?")
        
        return CandidateMessage(
            uid="123",
            uidvalidity="999",
            internaldate=None,
            message_id="<test-123@example.com>",
            raw_bytes=msg.as_bytes(),
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
    def mock_llm(self) -> MagicMock:
        """Create mock LLM gateway."""
        llm = MagicMock()
        from mail_ai_agent.schemas import LLMClassification, LLMEntities
        
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
    
    def test_full_pipeline_dry_run(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        candidate: CandidateMessage,
        fake_state: FakeStateManager,
        mock_audit: MagicMock,
        mock_llm: MagicMock,
    ) -> None:
        """Test full pipeline execution in dry-run mode."""
        # Build pipeline
        pipeline = ProcessingPipeline([
            ParseStage(settings),
            LeaseStage(fake_state),
            ClassifyStage(settings, mock_llm),
            RouteStage(settings, fake_state),
            AuditStage(mock_audit),
        ])
        
        # Execute
        result = pipeline.process(candidate, mailbox, settings)
        
        # Verify
        assert isinstance(result, ProcessingResult)
        assert result.final_status == WorkflowStatus.PROCESSED
        assert result.category == "question"
        assert result.confidence == 0.9
        assert result.stage_timings  # Should have timing data
        
        # Verify LLM was called
        mock_llm.classify.assert_called_once()
        
        # Verify audit was logged
        mock_audit.log.assert_called_once()
        audit_call = mock_audit.log.call_args
        assert audit_call.kwargs["mailbox_id"] == "test_mailbox"
        assert audit_call.kwargs["dry_run"] is True

    def test_pipeline_handles_llm_tuple_result_and_latency(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        candidate: CandidateMessage,
        fake_state: FakeStateManager,
        mock_audit: MagicMock,
    ) -> None:
        """Pipeline should accept LLM gateways returning (classification, latency_ms)."""
        from mail_ai_agent.schemas import LLMClassification, LLMEntities

        llm = MagicMock()
        llm.classify.return_value = (
            LLMClassification(
                category="question",
                priority="medium",
                requires_reply=False,
                confidence=0.91,
                summary="Tuple result",
                entities=LLMEntities(),
                draft_reply=None,
                reasoning_short="tuple",
            ),
            1234,
        )
        pipeline = ProcessingPipeline([
            ParseStage(settings),
            LeaseStage(fake_state),
            ClassifyStage(settings, llm),
            RouteStage(settings, fake_state),
            AuditStage(mock_audit),
        ])

        result = pipeline.process(candidate, mailbox, settings)
        assert result.final_status == WorkflowStatus.PROCESSED
        assert result.llm_latency_ms == 1234
    
    def test_pipeline_parse_failure(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        fake_state: FakeStateManager,
        mock_audit: MagicMock,
    ) -> None:
        """Test pipeline handles parse failure gracefully.
        
        Note: The email parser is very lenient, so we test the error handling
        path by directly injecting a parse error into the context.
        """
        from mail_ai_agent.pipeline.context import ProcessingContext
        from mail_ai_agent.pipeline.stages import AuditStage
        
        # Create a context with a parse error
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = "test@example.com"
        msg["Subject"] = "Test"
        msg.set_content("Test content")
        
        candidate = CandidateMessage(
            uid="999",
            uidvalidity="999",
            internaldate=None,
            message_id="<test@example.com>",
            raw_bytes=msg.as_bytes(),
        )
        
        context = ProcessingContext(
            candidate=candidate,
            mailbox=mailbox,
            settings=settings,
        )
        # Inject parse error
        context.parse_error = Exception("Simulated parse failure")
        
        # Run just the audit stage with the error context
        audit_stage = AuditStage(mock_audit)
        result_context = audit_stage.process(context)
        
        # Should produce FAILED result
        assert result_context.result is not None
        assert result_context.result.final_status == WorkflowStatus.FAILED
        assert "parse" in result_context.result.error.lower()
        
        # Audit should be logged
        mock_audit.log.assert_called_once()
    
    def test_pipeline_lease_already_done(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        candidate: CandidateMessage,
        fake_state: FakeStateManager,
        mock_audit: MagicMock,
        mock_llm: MagicMock,
    ) -> None:
        """Test pipeline when message already processed."""
        # Use dry_run=False so RouteStage actually marks state
        prod_settings = settings.model_copy(update={"dry_run": False})
        
        # First process the message
        pipeline = ProcessingPipeline([
            ParseStage(prod_settings),
            LeaseStage(fake_state),
            ClassifyStage(prod_settings, mock_llm),
            RouteStage(prod_settings, fake_state),
            AuditStage(mock_audit),
        ])
        
        result1 = pipeline.process(candidate, mailbox, prod_settings)
        assert result1.final_status == WorkflowStatus.PROCESSED
        
        # Reset audit mock
        mock_audit.reset_mock()
        
        # Try to process again
        result2 = pipeline.process(candidate, mailbox, prod_settings)
        
        # Should skip
        assert result2.final_status == WorkflowStatus.SKIPPED
        assert "already" in result2.error.lower()
    
    def test_pipeline_stage_timings(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        candidate: CandidateMessage,
        fake_state: FakeStateManager,
        mock_audit: MagicMock,
        mock_llm: MagicMock,
    ) -> None:
        """Test that pipeline records stage timings."""
        pipeline = ProcessingPipeline([
            ParseStage(settings),
            LeaseStage(fake_state),
            ClassifyStage(settings, mock_llm),
            AuditStage(mock_audit),
        ])
        
        result = pipeline.process(candidate, mailbox, settings)
        
        # Should have timing for each stage
        assert "parse" in result.stage_timings
        assert "lease" in result.stage_timings
        assert "classify" in result.stage_timings
        assert "audit" in result.stage_timings
        
        # Timings should be non-negative
        for stage_name, timing in result.stage_timings.items():
            assert timing >= 0, f"Stage {stage_name} has negative timing"
    
    def test_individual_parse_stage(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        candidate: CandidateMessage,
    ) -> None:
        """Test ParseStage in isolation."""
        from mail_ai_agent.pipeline.context import ProcessingContext
        
        stage = ParseStage(settings)
        context = ProcessingContext(
            candidate=candidate,
            mailbox=mailbox,
            settings=settings,
        )
        
        result = stage.process(context)
        
        assert result.parsed is not None
        assert result.parsed.sender == "sender@example.com"
        assert result.parsed.subject == "Test Question"
        assert result.fingerprint is not None
        assert "parse" in result.stage_timings
    
    def test_individual_lease_stage(
        self,
        settings: Settings,
        mailbox: MailboxConfig,
        candidate: CandidateMessage,
        fake_state: FakeStateManager,
    ) -> None:
        """Test LeaseStage in isolation."""
        from mail_ai_agent.pipeline.context import ProcessingContext
        
        # First parse
        parse_stage = ParseStage(settings)
        context = ProcessingContext(
            candidate=candidate,
            mailbox=mailbox,
            settings=settings,
        )
        context = parse_stage.process(context)
        
        # Then acquire lease
        lease_stage = LeaseStage(fake_state)
        context = lease_stage.process(context)
        
        assert context.lease is not None
        assert context.lease.outcome == "acquired"
        assert context.lease.record is not None
        assert context.is_lease_acquired
