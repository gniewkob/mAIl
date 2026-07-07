"""Pipeline stage implementations."""

from __future__ import annotations

import hashlib
import logging
from time import perf_counter
from typing import TYPE_CHECKING

from ..circuit_breaker import CircuitBreakerOpenError
from ..constants import ActionTaken, WorkflowStatus
from ..decision_engine import decide_from_llm, decide_from_rule
from ..email_parser import compute_content_fingerprint, compute_message_fingerprint, parse_email
from ..rule_engine import evaluate_rules
from ..folder_mapper import category_to_folder
from .base import StageError
from .context import ProcessingContext, ProcessingResult

if TYPE_CHECKING:
    from ..audit_logger import AuditLogger
    from ..config import Settings
    from ..draft_store import DraftStore
    from ..repositories.base import LeaseRepositoryProtocol, StateRepositoryProtocol

LOGGER = logging.getLogger(__name__)


class ParseStage:
    """Stage 1: Parse raw email bytes."""
    
    name = "parse"
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
    
    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Parse email and compute fingerprints."""
        try:
            parsed = parse_email(context.candidate.raw_bytes, self.settings)
            fingerprint = compute_message_fingerprint(parsed)
            content_fingerprint = compute_content_fingerprint(parsed)
            
            context.parsed = parsed
            context.fingerprint = fingerprint
            context.content_fingerprint = content_fingerprint
            context.record_timing(self.name)
            
        except (ValueError, TypeError, hashlib.HashlibError, LookupError) as exc:
            # Expected parsing errors (including encoding issues like cp-850)
            LOGGER.error("Failed to parse message: %s", exc)
            context.parse_error = exc
        except Exception as exc:
            # Unexpected error - log and mark as parse failure instead of crashing
            LOGGER.exception("Unexpected error during parsing (message isolated)")
            context.parse_error = exc
        
        return context


class LeaseStage:
    """Stage 2: Acquire processing lease."""
    
    name = "lease"
    
    def __init__(self, state_repository: LeaseRepositoryProtocol) -> None:
        """Initialize with lease repository.
        
        Args:
            state_repository: Object implementing LeaseRepositoryProtocol
        """
        self.state = state_repository
    
    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Acquire lease for processing."""
        # Skip if parse failed
        if context.is_parse_failed:
            return context
        
        if context.parsed is None:
            raise StageError("lease", "Cannot acquire lease: email not parsed")
        if context.fingerprint is None:
            raise StageError("lease", "Cannot acquire lease: fingerprint not computed")
        
        lease = self.state.acquire_lease(
            mailbox_id=context.mailbox.mailbox_id,
            message_id=context.candidate.message_id,
            fingerprint=context.fingerprint,
            content_fingerprint=context.content_fingerprint,
            imap_uid=context.candidate.uid,
            uidvalidity=context.candidate.uidvalidity,
            sender=context.parsed.sender,
            sender_sha256=None,  # Computed by repository if needed
            subject=context.parsed.subject,
            subject_sha256=None,
            source_folder=context.mailbox.imap_source_folder,
            internaldate=context.candidate.internaldate,
            worker_id=context.settings.worker_id,
            lease_seconds=context.settings.processing_lease_seconds,
            max_retries=context.settings.max_retries,
        )
        
        context.lease = lease
        context.record_timing(self.name)
        
        return context


class ClassifyStage:
    """Stage 3: Classify email (rules + LLM)."""
    
    name = "classify"
    
    def __init__(
        self,
        settings: Settings,
        llm_gateway,
    ) -> None:
        self.settings = settings
        self.llm = llm_gateway
    
    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Evaluate rules and call LLM if needed."""
        # Skip if lease not acquired
        if not context.is_lease_acquired:
            return context
        
        if context.parsed is None:
            raise StageError("classify", "Cannot classify: email not parsed")
        
        try:
            decision = self._classify(context)
            context.decision = decision
            context.record_timing(self.name)
            
        except CircuitBreakerOpenError as exc:
            LOGGER.warning("Circuit breaker open, routing to uncertain")
            context.decision = self._circuit_breaker_decision(context.mailbox)
            context.record_timing(self.name)
            
        except RuntimeError as exc:
            # LLM call failed
            LOGGER.error("LLM classification failed: %s", exc)
            context.classification_error = exc
        except Exception as exc:
            # Unexpected error - wrap it
            LOGGER.exception("Unexpected error during classification")
            raise StageError("classify", f"Unexpected classification error: {exc}") from exc
        
        return context
    
    def _classify(self, context: ProcessingContext) -> "FinalDecision":
        """Perform classification."""
        # Evaluate rules
        rule = evaluate_rules(context.parsed, context.mailbox)
        context.rule_hit = rule.reason if rule.action != "needs_llm" else None
        
        if rule.action != "needs_llm":
            # Use rule decision
            return decide_from_rule(rule)
        
        # Call LLM (supports both legacy classify(parsed) and newer classify(parsed, mailbox))
        classification, latency_ms = self._invoke_llm(context)
        context.llm_latency_ms = latency_ms
        
        return decide_from_llm(classification, self.settings, context.mailbox)

    def _invoke_llm(self, context: ProcessingContext):
        """Invoke LLM gateway with backward/forward compatible signatures."""
        llm_start = perf_counter()
        try:
            raw_result = self.llm.classify(context.parsed, context.mailbox)
        except TypeError:
            raw_result = self.llm.classify(context.parsed)

        measured_latency_ms = int((perf_counter() - llm_start) * 1000)
        if isinstance(raw_result, tuple) and len(raw_result) == 2:
            classification, latency_ms = raw_result
            return classification, int(latency_ms)
        return raw_result, measured_latency_ms
    
    def _circuit_breaker_decision(self, mailbox: "MailboxConfig") -> "FinalDecision":
        """Create uncertain decision when circuit breaker is open."""
        from ..schemas import FinalDecision
        
        return FinalDecision(
            category="other",
            priority="medium",
            confidence=0.0,
            target_folder=mailbox.imap_uncertain_folder,
            flags=[],
            final_status=WorkflowStatus.UNCERTAIN,
            action_taken=ActionTaken.ROUTE_UNCERTAIN,
            requires_reply=False,
            summary="Circuit breaker open - LLM unavailable",
            reasoning_short="Circuit breaker open",
        )


class RouteStage:
    """Stage 4: Execute IMAP routing (copy + delete).
    
    This is a base implementation that marks state transitions.
    For actual IMAP operations, use IMAPRouteStage from message_processor_v2.
    
    Note: This stage intentionally does not perform actual IMAP operations
    to allow for dry-run testing and simulation. The actual IMAP routing
    is implemented in IMAPRouteStage which extends this class.
    """
    
    name = "route"
    
    def __init__(
        self,
        settings: Settings,
        state_repository: StateRepositoryProtocol,
        draft_store: DraftStore | None = None,
    ) -> None:
        self.settings = settings
        self.state = state_repository
        self.drafts = draft_store
    
    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Execute routing decision."""
        # Skip if no decision
        if context.decision is None:
            return context
        
        # Dry run - no actual routing
        if self.settings.dry_run:
            context.record_timing(self.name)
            return context
        
        try:
            self._execute_routing(context)
            context.record_timing(self.name)
            
        except (RuntimeError, OSError, IOError) as exc:
            # IMAP errors, file system errors, etc.
            LOGGER.error("Routing failed: %s", exc)
            context.routing_error = exc
        except Exception as exc:
            # Unexpected error - wrap it
            LOGGER.exception("Unexpected error during routing")
            raise StageError("route", f"Unexpected routing error: {exc}") from exc
        
        return context
    
    def _execute_routing(self, context: ProcessingContext) -> None:
        """Execute the routing operation."""
        if context.lease is None:
            raise StageError("route", "Cannot route: lease not acquired")
        if context.lease.record is None:
            raise StageError("route", "Cannot route: lease record is None")
        if context.decision is None:
            raise StageError("route", "Cannot route: no decision available")
        
        record_id = context.lease.record.id
        
        # Save draft if needed
        draft_path = None
        if (
            context.decision.draft_reply
            and context.decision.requires_reply
            and self.drafts
        ):
            draft_path = self.drafts.save(context.parsed, context.decision, context.fingerprint)
            context.draft_path = draft_path
        
        # Determine target folder
        if context.decision.final_status == WorkflowStatus.UNCERTAIN:
            target_folder = context.mailbox.imap_uncertain_folder
        else:
            target_folder = category_to_folder(context.decision.category, context.mailbox)
        
        context.target_folder = target_folder
        
        # Mark state transition (without actual IMAP operations)
        # This allows the pipeline to be tested without IMAP
        # For production use with IMAP, see IMAPRouteStage in message_processor_v2
        if context.decision.final_status == WorkflowStatus.UNCERTAIN:
            self.state.mark_uncertain(
                record_id,
                category=context.decision.category,
                confidence=context.decision.confidence,
                target_folder=target_folder,
                target_uid=None,  # Would be set after IMAP copy
                action_taken=ActionTaken.MOVE_ROUTE_UNCERTAIN.value,
                error_message=context.decision.reasoning_short if hasattr(context.decision, 'reasoning_short') else None,
            )
        else:
            self.state.mark_processed(
                record_id,
                category=context.decision.category,
                confidence=context.decision.confidence,
                target_folder=target_folder,
                target_uid=None,
                action_taken=ActionTaken.MOVE_ROUTE_REPLY.value,
                draft_path=draft_path,
                rule_hit=context.rule_hit,
                model_name=self.settings.ollama_model if context.llm_latency_ms else None,
                model_latency_ms=context.llm_latency_ms,
            )


class AuditStage:
    """Stage 5: Log audit entry."""
    
    name = "audit"
    
    def __init__(self, audit_logger: AuditLogger) -> None:
        self.audit = audit_logger
    
    def process(self, context: ProcessingContext) -> ProcessingContext:
        """Log audit entry based on processing outcome."""
        result = self._create_result(context)
        context.result = result
        
        # Log audit
        self._log_audit(context, result)
        
        context.record_timing(self.name)
        return context
    
    def _create_result(self, context: ProcessingContext) -> ProcessingResult:
        """Create processing result from context."""
        # Parse failure
        if context.is_parse_failed:
            return ProcessingResult(
                action_taken=ActionTaken.FAILED_PARSE,
                final_status=WorkflowStatus.FAILED,
                latency_ms=context.total_duration_ms,
                error=str(context.parse_error),
                stage_timings=context.stage_timings,
            )
        
        # Lease not acquired
        if context.lease and context.lease.outcome != "acquired":
            action_map = {
                "already_done": ActionTaken.SKIP_ALREADY_DONE,
                "locked": ActionTaken.SKIP_LOCKED,
                "conflict": ActionTaken.SKIP_CONFLICT,
            }
            return ProcessingResult(
                action_taken=action_map.get(context.lease.outcome, ActionTaken.SKIP_ALREADY_DONE),
                final_status=WorkflowStatus.SKIPPED,
                latency_ms=context.total_duration_ms,
                error=context.lease.reason,
                stage_timings=context.stage_timings,
            )
        
        # Classification failure
        if context.is_classification_failed:
            return ProcessingResult(
                action_taken=ActionTaken.FAILED_CLASSIFY,
                final_status=WorkflowStatus.FAILED,
                latency_ms=context.total_duration_ms,
                error=str(context.classification_error),
                stage_timings=context.stage_timings,
            )
        
        # Routing failure
        if context.is_routing_failed:
            return ProcessingResult(
                action_taken=ActionTaken.FAILED_ROUTE,
                final_status=WorkflowStatus.FAILED,
                latency_ms=context.total_duration_ms,
                error=str(context.routing_error),
                stage_timings=context.stage_timings,
            )
        
        # Success - but may not have decision if pipeline was short
        if context.decision is None:
            # Pipeline completed without decision (e.g., dry run with no routing)
            return ProcessingResult(
                action_taken=ActionTaken.SKIP_ALREADY_DONE,
                final_status=WorkflowStatus.SKIPPED,
                latency_ms=context.total_duration_ms,
                error="Pipeline completed without decision",
                stage_timings=context.stage_timings,
            )
        
        action = self._determine_action(context)
        
        return ProcessingResult(
            action_taken=action,
            final_status=context.decision.final_status,
            category=context.decision.category,
            confidence=context.decision.confidence,
            target_folder=context.decision.target_folder,
            draft_path=context.draft_path,
            latency_ms=context.total_duration_ms,
            stage_timings=context.stage_timings,
            rule_hit=context.rule_hit,
            llm_latency_ms=context.llm_latency_ms,
        )
    
    def _determine_action(self, context: ProcessingContext) -> ActionTaken:
        """Determine the action taken based on context."""
        if context.decision is None:
            raise StageError("audit", "Cannot determine action: no decision available")
        
        if context.settings.dry_run:
            return ActionTaken.SIMULATE_ROUTE_REPLY
        
        if context.decision.final_status == WorkflowStatus.UNCERTAIN:
            return ActionTaken.ROUTE_UNCERTAIN
        
        return ActionTaken.ROUTE_REPLY
    
    def _log_audit(self, context: ProcessingContext, result: ProcessingResult) -> None:
        """Log audit entry."""
        level = "ERROR" if result.final_status == WorkflowStatus.FAILED else "INFO"
        
        try:
            self.audit.log(
            level=level,
            mailbox_id=context.mailbox.mailbox_id,
            mailbox_user=context.mailbox.imap_user,
            source_folder=context.mailbox.imap_source_folder,
            message_id=context.candidate.message_id,
            fingerprint=context.fingerprint or "",
            imap_uid=context.candidate.uid,
            sender=context.parsed.sender if context.parsed else None,
            subject=context.parsed.subject if context.parsed else None,
            status_before="new",
            status_after=result.final_status.value,
            action_taken=result.action_taken.value if isinstance(result.action_taken, ActionTaken) else result.action_taken,
            category=result.category,
            confidence=result.confidence,
            target_folder=result.target_folder,
            draft_path=result.draft_path,
            duration_ms=result.latency_ms,
            error=result.error,
            dry_run=context.settings.dry_run,
            )
        except Exception as exc:
            # Audit logging should never break processing
            # But we should log the failure
            LOGGER.error("Failed to write audit log: %s", exc)
