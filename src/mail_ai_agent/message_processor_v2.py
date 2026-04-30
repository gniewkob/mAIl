"""MessageProcessor v2 - refactored to use Pipeline internally.

This version maintains backward compatibility with the original API
while using the new pipeline architecture internally.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

from .constants import ActionTaken, WorkflowStatus
from .pipeline import (
    AuditStage,
    ClassifyStage,
    LeaseStage,
    ParseStage,
    ProcessingPipeline,
    RouteStage as PipelineRouteStage,
)
from .pipeline.context import ProcessingResult as PipelineProcessingResult
from .schemas import CandidateMessage, WorkflowStatus as SchemaWorkflowStatus

if TYPE_CHECKING:
    from .audit_logger import AuditLogger
    from .config import MailboxConfig, Settings
    from .draft_store import DraftStore
    from .imap_client import IMAPClient
    from .llm_gateway import LLMGateway
    from .schemas import EmailRecord, ParsedEmail
    from .state_manager import StateManager

LOGGER = logging.getLogger(__name__)


class ProcessingError(Exception):
    """Error during message processing."""
    pass


@dataclass(frozen=True)
class ProcessingResult:
    """Result of processing a single message (backward compatible)."""
    action_taken: str
    final_status: SchemaWorkflowStatus
    category: str | None
    confidence: float | None
    target_folder: str | None
    draft_path: str | None
    latency_ms: int
    error: str | None = None
    
    @classmethod
    def from_pipeline_result(cls, result: PipelineProcessingResult) -> "ProcessingResult":
        """Convert pipeline result to backward-compatible format."""
        action = result.action_taken
        if isinstance(action, ActionTaken):
            action = action.value
        
        return cls(
            action_taken=action,
            final_status=result.final_status,
            category=result.category,
            confidence=result.confidence,
            target_folder=result.target_folder,
            draft_path=result.draft_path,
            latency_ms=result.latency_ms,
            error=result.error,
        )


class RouteStage(PipelineRouteStage):
    """Extended RouteStage that handles IMAP operations."""
    
    def __init__(
        self,
        settings: Settings,
        state_repository,
        imap: IMAPClient,
        draft_store=None,
    ) -> None:
        super().__init__(settings, state_repository, draft_store)
        self.imap = imap
    
    def _execute_routing(self, context) -> None:
        """Execute routing with actual IMAP operations."""
        from .folder_mapper import category_to_folder
        
        if context.lease is None:
            raise ProcessingError("Cannot execute routing: lease not acquired")
        if context.lease.record is None:
            raise ProcessingError("Cannot execute routing: lease record is None")
        if context.decision is None:
            raise ProcessingError("Cannot execute routing: no decision available")
        
        record_id = context.lease.record.id
        
        # Save draft if needed
        draft_path = None
        if (
            context.decision.draft_reply
            and context.decision.requires_reply
            and self.drafts
        ):
            draft_path = self.drafts.save(
                context.parsed, context.decision, context.fingerprint
            )
            context.draft_path = draft_path
        
        # Determine target folder
        if context.decision.final_status == WorkflowStatus.UNCERTAIN:
            target_folder = context.mailbox.imap_uncertain_folder
        else:
            target_folder = category_to_folder(context.decision.category, context.mailbox)
        
        context.target_folder = target_folder
        
        # Execute IMAP operations
        try:
            target_uid = self.imap.copy_message(
                context.mailbox.imap_source_folder,
                context.candidate.uid,
                target_folder,
            )
            context.target_uid = target_uid
            
            try:
                self.imap.delete_message(context.mailbox.imap_source_folder, context.candidate.uid)
                
                # Success - mark as processed/uncertain
                if context.decision.final_status == WorkflowStatus.UNCERTAIN:
                    self.state.mark_uncertain(
                        record_id,
                        category=context.decision.category,
                        confidence=context.decision.confidence,
                        target_folder=target_folder,
                        target_uid=target_uid,
                        action_taken=ActionTaken.MOVE_ROUTE_UNCERTAIN.value,
                        error_message=getattr(context.decision, 'reasoning_short', None),
                    )
                else:
                    self.state.mark_processed(
                        record_id,
                        category=context.decision.category,
                        confidence=context.decision.confidence,
                        target_folder=target_folder,
                        target_uid=target_uid,
                        action_taken=ActionTaken.MOVE_ROUTE_REPLY.value,
                        draft_path=draft_path,
                        rule_hit=context.rule_hit,
                        model_name=self.settings.ollama_model if context.llm_latency_ms else None,
                        model_latency_ms=context.llm_latency_ms,
                    )
                    
                    # Set flag if needed
                    if target_uid and "\\Flagged" in getattr(context.decision, 'flags', []):
                        try:
                            self.imap.set_flagged(target_folder, target_uid)
                        except Exception as flag_exc:
                            LOGGER.warning("Failed to set flag: %s", flag_exc)
                            
            except Exception as delete_exc:
                # Copy succeeded but delete failed - mark cleanup pending
                LOGGER.error("Copy succeeded but source cleanup failed")
                self.state.mark_move_cleanup_pending(
                    record_id,
                    category=context.decision.category,
                    confidence=context.decision.confidence,
                    target_folder=target_folder,
                    target_uid=target_uid,
                    draft_path=draft_path,
                    rule_hit=context.rule_hit,
                    model_name=self.settings.ollama_model if context.llm_latency_ms else None,
                    model_latency_ms=context.llm_latency_ms,
                    error_message=str(delete_exc),
                    error_type=delete_exc.__class__.__name__,
                )
                
        except Exception as copy_exc:
            # Copy failed - mark as failed
            LOGGER.error("Failed to copy message: %s", copy_exc)
            self.state.mark_failed(
                record_id,
                error_message=f"copy_failed: {copy_exc}",
                error_type=copy_exc.__class__.__name__,
            )
            raise


class MessageProcessorV2:
    """Message processor using pipeline architecture internally.
    
    Maintains backward compatibility with original MessageProcessor API.
    """
    
    def __init__(
        self,
        settings: Settings,
        state: StateManager,
        audit: AuditLogger,
        drafts: DraftStore,
        llm: LLMGateway,
    ) -> None:
        self.settings = settings
        self.state = state
        self.audit = audit
        self.drafts = drafts
        self.llm = llm
    
    def process_candidate(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
    ) -> ProcessingResult:
        """Process a single email candidate through the full workflow.
        
        This method uses the new Pipeline internally while maintaining
        the same API as the original MessageProcessor.
        """
        # Build pipeline with IMAP-aware RouteStage
        pipeline = ProcessingPipeline([
            ParseStage(self.settings),
            LeaseStage(self.state),
            ClassifyStage(self.settings, self.llm),
            RouteStage(self.settings, self.state, imap, self.drafts),
            AuditStage(self.audit),
        ])
        
        # Execute pipeline
        result = pipeline.process(candidate, mailbox, self.settings)
        
        # Convert to backward-compatible format
        return ProcessingResult.from_pipeline_result(result)
    
    def process_candidate_simulate(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
    ) -> ProcessingResult:
        """Simulate processing without IMAP operations (dry run).
        
        Useful for testing and validation.
        """
        # Build pipeline without RouteStage (just simulation)
        pipeline = ProcessingPipeline([
            ParseStage(self.settings),
            LeaseStage(self.state),
            ClassifyStage(self.settings, self.llm),
            AuditStage(self.audit),
        ])
        
        # Temporarily set dry_run to True for this call
        original_dry_run = self.settings.dry_run
        self.settings.dry_run = True
        
        try:
            result = pipeline.process(candidate, mailbox, self.settings)
            return ProcessingResult.from_pipeline_result(result)
        finally:
            self.settings.dry_run = original_dry_run


# Factory function for easy migration
def create_processor(
    settings: Settings,
    state: StateManager,
    audit: AuditLogger,
    drafts: DraftStore,
    llm: LLMGateway,
    use_pipeline: bool = True,
) -> "MessageProcessorV2":
    """Create message processor.
    
    Args:
        settings: Application settings
        state: State manager
        audit: Audit logger
        drafts: Draft store
        llm: LLM gateway
        use_pipeline: If True, use new pipeline-based processor (default)
    
    Returns:
        Message processor instance
    """
    if use_pipeline:
        return MessageProcessorV2(settings, state, audit, drafts, llm)
    else:
        # Import original for backward compatibility
        from .message_processor import MessageProcessor
        return MessageProcessor(settings, state, audit, drafts, llm)
