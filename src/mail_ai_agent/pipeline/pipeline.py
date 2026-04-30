"""Pipeline orchestrator for email processing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

from ..constants import ActionTaken, WorkflowStatus
from .base import Stage, StageError
from .context import ProcessingContext, ProcessingResult

if TYPE_CHECKING:
    from ..config import MailboxConfig, Settings
    from ..schemas import CandidateMessage

LOGGER = logging.getLogger(__name__)


class ProcessingPipeline:
    """Pipeline for processing email candidates.
    
    The pipeline executes stages in sequence, passing a context object
    between them. Each stage can modify the context and/or abort processing.
    
    Example:
        pipeline = ProcessingPipeline([
            ParseStage(settings),
            LeaseStage(state_repo),
            ClassifyStage(settings, llm),
            RouteStage(settings, state_repo, drafts),
            AuditStage(audit),
        ])
        
        result = pipeline.process(candidate, mailbox, settings)
    """
    
    def __init__(self, stages: List[Stage]) -> None:
        """Initialize pipeline with stages.
        
        Args:
            stages: List of stage instances to execute in order
        """
        self.stages = stages
    
    def process(
        self,
        candidate: "CandidateMessage",
        mailbox: "MailboxConfig",
        settings: "Settings",
    ) -> ProcessingResult:
        """Process a candidate through all stages.
        
        Args:
            candidate: Email candidate to process
            mailbox: Mailbox configuration
            settings: Application settings
            
        Returns:
            ProcessingResult with outcome and metadata
        """
        context = ProcessingContext(
            candidate=candidate,
            mailbox=mailbox,
            settings=settings,
        )
        
        # Find audit stage if present (should always be last)
        audit_stage = None
        processing_stages = []
        for stage in self.stages:
            if stage.name == "audit":
                audit_stage = stage
            else:
                processing_stages.append(stage)
        
        try:
            # Run processing stages
            for stage in processing_stages:
                LOGGER.debug("Executing stage: %s", stage.name)
                context = stage.process(context)
                
                # Check for early termination conditions
                if self._should_terminate_early(context):
                    LOGGER.debug("Early termination after stage: %s", stage.name)
                    break
            
            # Always run audit stage if present
            if audit_stage:
                LOGGER.debug("Executing audit stage")
                context = audit_stage.process(context)
        
        except StageError as exc:
            LOGGER.error("Stage error in %s: %s", exc.stage_name, exc)
            # Ensure we have a result even on stage error
            if context.result is None:
                context.result = ProcessingResult(
                    action_taken=ActionTaken.FAILED,
                    final_status=WorkflowStatus.FAILED,
                    latency_ms=context.total_duration_ms,
                    error=str(exc),
                    stage_timings=context.stage_timings,
                )
        
        except Exception as exc:
            LOGGER.exception("Unexpected error in pipeline")
            if context.result is None:
                context.result = ProcessingResult(
                    action_taken=ActionTaken.FAILED,
                    final_status=WorkflowStatus.FAILED,
                    latency_ms=context.total_duration_ms,
                    error=f"Pipeline error: {exc}",
                    stage_timings=context.stage_timings,
                )
        
        # Ensure we always return a result
        if context.result is None:
            context.result = ProcessingResult(
                action_taken=ActionTaken.FAILED,
                final_status=WorkflowStatus.FAILED,
                latency_ms=context.total_duration_ms,
                error="No result produced by pipeline",
                stage_timings=context.stage_timings,
            )
        
        return context.result
    
    def _should_terminate_early(self, context: ProcessingContext) -> bool:
        """Determine if pipeline should terminate before all stages.
        
        Early termination conditions:
        - Parse failed (can't process without parsed email)
        - Lease not acquired (message already processed or locked)
        """
        # Parse failure - can't continue
        if context.is_parse_failed:
            return True
        
        # Lease not acquired - skip remaining stages
        if context.lease is not None and context.lease.outcome != "acquired":
            return True
        
        return False
