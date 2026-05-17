from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

from .circuit_breaker import CircuitBreakerOpenError
from .decision_engine import decide_from_llm, decide_from_rule
from .email_parser import compute_content_fingerprint, compute_message_fingerprint, parse_email
from .folder_mapper import category_to_folder
from .rule_engine import evaluate_rules
from .constants import ActionTaken
from .schemas import CandidateMessage, FinalDecision, LeaseAcquireResult, ParsedEmail, WorkflowStatus
from .source_cleanup import SourceCleanupHandler
from .utils import _hash_value

if TYPE_CHECKING:
    from .audit_logger import AuditLogger
    from .config import MailboxConfig, Settings
    from .draft_store import DraftStore
    from .imap_client import IMAPClient
    from .llm_gateway import LLMGateway
    from .state_manager import StateManager

LOGGER = logging.getLogger(__name__)

MOVE_CLEANUP_PENDING_ACTION = ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING


@dataclass(frozen=True)
class ProcessingResult:
    """Result of processing a single message."""
    action_taken: str
    final_status: WorkflowStatus
    category: str | None
    confidence: float | None
    target_folder: str | None
    draft_path: str | None
    latency_ms: int
    error: str | None = None


@dataclass(frozen=True)
class RoutingAction:
    """Represents the routing decision for a message."""
    action: str
    target_folder: str
    requires_flag: bool = False
    draft_reply: str | None = None
    category: str = "unknown"
    confidence: float = 0.0
    summary: str | None = None
    reasoning_short: str | None = None


class MessageProcessor:
    """Handles the processing of individual email messages.
    
    Responsibilities:
    - Parse email and compute fingerprints
    - Evaluate rules or call LLM for classification
    - Make routing decisions
    - Execute IMAP operations (copy/delete/flag)
    - Manage state transitions
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
        self.cleanup_handler = SourceCleanupHandler(state, audit, settings.worker_id)
    
    def process_candidate(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
    ) -> ProcessingResult:
        """Process a single email candidate through the full workflow."""
        started = perf_counter()
        
        # Step 1: Parse email
        try:
            parsed = parse_email(candidate.raw_bytes, self.settings)
            fingerprint = compute_message_fingerprint(parsed)
            content_fingerprint = compute_content_fingerprint(parsed)
        except Exception as exc:
            LOGGER.exception("Failed to parse message for mailbox %s", mailbox.mailbox_id)
            return self._handle_parse_failure(
                candidate, mailbox, imap, started, exc
            )
        
        # In dry_run mode, just simulate without persisting state
        if self.settings.dry_run:
            return self._simulate_processing(
                candidate, mailbox, started, parsed, fingerprint
            )
        
        # Step 2: Acquire lease (only in production mode)
        lease = self._acquire_lease(
            candidate, mailbox, parsed, fingerprint, content_fingerprint
        )
        
        if lease.outcome != "acquired":
            return self._handle_lease_failed(
                candidate, mailbox, imap, started, lease, parsed, fingerprint, content_fingerprint,
                sender=parsed.sender,
                subject=parsed.subject,
            )
        
        record = lease.record
        if record is None:
            raise RuntimeError("Lease acquired but record is None")
        
        # Evaluate rules (needed for both routing and error handling)
        rule = evaluate_rules(parsed, mailbox)
        
        # Step 3: Classify and route
        try:
            return self._classify_and_route(
                candidate, mailbox, imap, started, parsed, fingerprint, record, rule
            )
        except CircuitBreakerOpenError as exc:
            # LLM circuit breaker is open - route to uncertain
            return self._handle_llm_circuit_breaker(
                candidate, mailbox, imap, started, parsed, fingerprint, record, exc
            )
        except RuntimeError as exc:
            # LLM failure - route to uncertain if configured
            if self.settings.llm_failure_route_to_uncertain:
                return self._handle_llm_failure(
                    candidate, mailbox, imap, started, parsed, fingerprint, record, exc
                )
            raise
        except Exception as exc:
            # Unexpected error during classification/routing
            return self._handle_classification_error(
                candidate, mailbox, started, parsed, fingerprint, record, exc
            )
    
    def _simulate_processing(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        started: float,
        parsed: ParsedEmail,
        fingerprint: str,
    ) -> ProcessingResult:
        """Simulate processing without persisting state (dry_run mode)."""
        from .decision_engine import decide_from_llm, decide_from_rule
        from .rule_engine import evaluate_rules
        
        try:
            # Evaluate rules
            rule = evaluate_rules(parsed, mailbox)
            
            if rule.action == "needs_llm":
                # In dry_run, don't call LLM - just simulate
                # We'll return a simulated LLM result
                decision = self._simulate_llm_decision(parsed, mailbox)
            else:
                decision = decide_from_rule(rule)
            
            duration_ms = int((perf_counter() - started) * 1000)
            
            # Dry run audit logging is done in main.py
            
            return ProcessingResult(
                action_taken=f"simulate_{decision.action_taken}",
                final_status=decision.final_status,
                category=decision.category,
                confidence=decision.confidence,
                target_folder=decision.target_folder,
                draft_path=None,
                latency_ms=duration_ms,
                error=None,
            )
            
        except Exception as exc:
            LOGGER.exception("Dry run simulation failed")
            duration_ms = int((perf_counter() - started) * 1000)
            return ProcessingResult(
                action_taken=ActionTaken.SIMULATE_FAILED,
                final_status=WorkflowStatus.FAILED,
                category=None,
                confidence=None,
                target_folder=None,
                draft_path=None,
                latency_ms=duration_ms,
                error=str(exc),
            )
    
    def _simulate_llm_decision(self, parsed: ParsedEmail, mailbox: MailboxConfig) -> "FinalDecision":
        """Simulate LLM decision for dry_run mode."""
        from .decision_engine import decide_from_llm
        from .schemas import LLMClassification
        
        # Create a simulated classification
        classification = LLMClassification(
            category="question",  # Default simulation
            priority="medium",
            requires_reply=False,
            confidence=0.85,
            summary=f"Simulated classification for: {parsed.subject[:50]}",
            entities={},
            draft_reply=None,
            reasoning_short="Simulated LLM decision in dry_run mode",
        )
        
        return decide_from_llm(classification, self.settings, mailbox)
    
    def _handle_parse_failure(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
        started: float,
        exc: Exception,
    ) -> ProcessingResult:
        """Handle email parsing failure by quarantining to uncertain folder."""
        fingerprint = hashlib.sha256(candidate.raw_bytes).hexdigest()
        duration_ms = int((perf_counter() - started) * 1000)
        
        if self.settings.dry_run:
            self.audit.log(
                level="ERROR",
                mailbox_id=mailbox.mailbox_id,
                mailbox_user=mailbox.imap_user,
                source_folder=mailbox.imap_source_folder,
                message_id=candidate.message_id,
                fingerprint=fingerprint,
                imap_uid=candidate.uid,
                status_before=None,
                status_after="failed",
                action_taken=ActionTaken.FAILED_PARSE,
                duration_ms=duration_ms,
                error=str(exc),
                dry_run=True,
            )
            return ProcessingResult(
                action_taken=ActionTaken.FAILED_PARSE,
                final_status=WorkflowStatus.FAILED,
                category=None,
                confidence=None,
                target_folder=None,
                draft_path=None,
                latency_ms=duration_ms,
                error=str(exc),
            )
        
        # In production, try to quarantine
        quarantine_result = self._try_quarantine_parse_failure(
            candidate, mailbox, imap, fingerprint, exc
        )
        
        if quarantine_result is None:
            # Message already processed (likely already quarantined)
            # Log and return skip result
            self.audit.log(
                level="INFO",
                mailbox_id=mailbox.mailbox_id,
                mailbox_user=mailbox.imap_user,
                source_folder=mailbox.imap_source_folder,
                message_id=candidate.message_id,
                fingerprint=fingerprint,
                imap_uid=candidate.uid,
                sender=None,
                subject=None,
                status_before=WorkflowStatus.UNCERTAIN.value,
                status_after=WorkflowStatus.UNCERTAIN.value,
                action_taken=ActionTaken.SKIP_ALREADY_DONE,
                duration_ms=duration_ms,
                error="Parse failure message already quarantined",
                dry_run=False,
            )
            return ProcessingResult(
                action_taken=ActionTaken.SKIP_ALREADY_DONE,
                final_status=WorkflowStatus.SKIPPED,
                category=None,
                confidence=None,
                target_folder=None,
                draft_path=None,
                latency_ms=duration_ms,
                error="Message already processed (parse failure)",
            )
        
        # Log successful quarantine
        self.audit.log(
            level="ERROR",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=candidate.message_id,
            fingerprint=fingerprint,
            imap_uid=candidate.uid,
            sender=None,
            subject=None,
            status_before="processing",
            status_after=WorkflowStatus.UNCERTAIN.value,
            category="parse_error",
            confidence=0.0,
            action_taken=ActionTaken.MOVE_ROUTE_UNCERTAIN_PARSE_FAILURE,
            target_folder=mailbox.imap_uncertain_folder,
            duration_ms=duration_ms,
            error=f"parse_failed: {exc}",
            dry_run=False,
        )
        
        return ProcessingResult(
            action_taken=ActionTaken.MOVE_ROUTE_UNCERTAIN_PARSE_FAILURE,
            final_status=WorkflowStatus.UNCERTAIN,
            category="parse_error",
            confidence=0.0,
            target_folder=mailbox.imap_uncertain_folder,
            draft_path=None,
            latency_ms=duration_ms,
            error=str(exc),
        )
    
    def _try_quarantine_parse_failure(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
        fingerprint: str,
        parse_error: Exception,
    ) -> str | None:
        """Try to quarantine a message that failed to parse."""
        # Acquire lease for cleanup tracking
        lease = self.state.acquire_lease(
            mailbox_id=mailbox.mailbox_id,
            message_id=candidate.message_id,
            fingerprint=fingerprint,
            content_fingerprint=None,
            imap_uid=candidate.uid,
            uidvalidity=candidate.uidvalidity,
            sender="",
            sender_sha256=None,
            subject="",
            subject_sha256=None,
            source_folder=mailbox.imap_source_folder,
            internaldate=candidate.internaldate,
            worker_id=self.settings.worker_id,
            lease_seconds=self.settings.processing_lease_seconds,
            max_retries=self.settings.max_retries,
        )
        
        if lease.outcome != "acquired":
            LOGGER.warning("Could not acquire lease for parse failure quarantine: %s", lease.reason)
            return None
        
        record = lease.record
        if record is None:
            raise RuntimeError("Lease acquired but record is None in quarantine")
        
        try:
            target_uid = imap.copy_message(
                mailbox.imap_source_folder,
                candidate.uid,
                mailbox.imap_uncertain_folder,
            )
            try:
                imap.delete_message(mailbox.imap_source_folder, candidate.uid)
            except Exception as cleanup_exc:
                LOGGER.exception("Parse failure quarantine cleanup failed")
                self.state.mark_move_cleanup_pending(
                    record.id,
                    category="parse_error",
                    confidence=0.0,
                    target_folder=mailbox.imap_uncertain_folder,
                    target_uid=target_uid,
                    draft_path=None,
                    rule_hit=None,
                    model_name=None,
                    model_latency_ms=None,
                    error_message=f"parse_failed: {parse_error}; cleanup_failed: {cleanup_exc}",
                    error_type=cleanup_exc.__class__.__name__,
                )
                self.audit.log(
                    level="ERROR",
                    mailbox_id=mailbox.mailbox_id,
                    mailbox_user=mailbox.imap_user,
                    source_folder=mailbox.imap_source_folder,
                    message_id=candidate.message_id,
                    fingerprint=fingerprint,
                    imap_uid=candidate.uid,
                    status_before="processing",
                    status_after=WorkflowStatus.CLEANUP_PENDING.value,
                    category="parse_error",
                    confidence=0.0,
                    action_taken=MOVE_CLEANUP_PENDING_ACTION,
                    target_folder=mailbox.imap_uncertain_folder,
                    duration_ms=0,
                    error=f"parse_failed: {parse_error}; cleanup_failed: {cleanup_exc}",
                    dry_run=False,
                )
                return target_uid
            
            self.state.mark_uncertain(
                record.id,
                category="parse_error",
                confidence=0.0,
                target_folder=mailbox.imap_uncertain_folder,
                target_uid=target_uid,
                action_taken=ActionTaken.MOVE_ROUTE_UNCERTAIN_PARSE_FAILURE,
                error_message=f"parse_failed: {parse_error}",
            )
            return target_uid
            
        except Exception as move_exc:
            LOGGER.exception("Failed to quarantine parse-failed message")
            self.state.mark_failed(
                record.id,
                error_message=f"parse_failed: {parse_error}; quarantine_failed: {move_exc}",
                error_type=move_exc.__class__.__name__,
            )
            return None
    
    def _acquire_lease(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        parsed: ParsedEmail,
        fingerprint: str,
        content_fingerprint: str,
    ) -> LeaseAcquireResult:
        """Acquire processing lease for the message."""
        return self.state.acquire_lease(
            mailbox_id=mailbox.mailbox_id,
            message_id=parsed.message_id,
            fingerprint=fingerprint,
            content_fingerprint=content_fingerprint,
            imap_uid=candidate.uid,
            uidvalidity=candidate.uidvalidity,
            sender=self._state_identity(parsed.sender, redact=self.settings.state_redact_pii),
            sender_sha256=_hash_value(parsed.sender),
            subject=self._state_identity(parsed.subject, redact=self.settings.state_redact_pii),
            subject_sha256=_hash_value(parsed.subject),
            source_folder=mailbox.imap_source_folder,
            internaldate=candidate.internaldate,
            worker_id=self.settings.worker_id,
            lease_seconds=self.settings.processing_lease_seconds,
            max_retries=self.settings.max_retries,
        )
    
    def _handle_lease_failed(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
        started: float,
        lease: LeaseAcquireResult,
        parsed: ParsedEmail,
        fingerprint: str,
        content_fingerprint: str,
        sender: str | None,
        subject: str | None,
    ) -> ProcessingResult:
        """Handle lease acquisition failure."""
        duration_ms = int((perf_counter() - started) * 1000)
        
        # Try cleanup for already-processed messages
        cleaned = self.cleanup_handler.try_cleanup_already_processed(
            candidate, mailbox, imap, lease,
            parsed.message_id, fingerprint, content_fingerprint, sender, subject
        )
        if not cleaned:
            cleaned = self.cleanup_handler.try_cleanup_processed_conflict(
                candidate, mailbox, imap, lease,
                parsed.message_id, fingerprint, content_fingerprint, sender or "", subject or ""
            )
        
        if cleaned:
            # Cleanup was successful
            if lease.outcome == "already_done":
                return ProcessingResult(
                    action_taken=ActionTaken.CLEANUP_SOURCE_ALREADY_DONE,
                    final_status=WorkflowStatus.SKIPPED,
                    category=None,
                    confidence=None,
                    target_folder=None,
                    draft_path=None,
                    latency_ms=duration_ms,
                    error=None,
                )
            elif lease.outcome == "conflict":
                return ProcessingResult(
                    action_taken=ActionTaken.CLEANUP_SOURCE_CONFLICT_DUPLICATE,
                    final_status=WorkflowStatus.SKIPPED,
                    category=None,
                    confidence=None,
                    target_folder=None,
                    draft_path=None,
                    latency_ms=duration_ms,
                    error=None,
                )
        
        self.audit.log(
            level="INFO",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=parsed.message_id,
            fingerprint=fingerprint,
            imap_uid=candidate.uid,
            sender=sender,
            subject=subject,
            status_before=lease.record.status.value if lease.record else None,
            status_after=lease.record.status.value if lease.record else None,
            action_taken=f"skip_{lease.outcome}",
            duration_ms=duration_ms,
            error=lease.reason,
            dry_run=self.settings.dry_run,
        )
        
        return ProcessingResult(
            action_taken=f"skip_{lease.outcome}",
            final_status=lease.record.status if lease.record else WorkflowStatus.SKIPPED,
            category=None,
            confidence=None,
            target_folder=None,
            draft_path=None,
            latency_ms=duration_ms,
            error=lease.reason,
        )
    
    def _classify_and_route(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
        started: float,
        parsed: ParsedEmail,
        fingerprint: str,
        record: "EmailRecord",
        rule: "RuleDecision",
    ) -> ProcessingResult:
        """Classify message and execute routing."""
        # Rule already evaluated in process_candidate
        draft_path = None
        latency_ms = None
        
        if rule.action == "needs_llm":
            # Call LLM for classification
            classification, latency_ms = self.llm.classify(parsed)
            decision = decide_from_llm(classification, self.settings, mailbox)
        else:
            decision = decide_from_rule(rule)
        
        # Save draft if needed
        if decision.draft_reply:
            draft_path = str(self.drafts.save(
                parsed_email=parsed,
                decision=decision,
                fingerprint=fingerprint,
                redact_pii=self.settings.state_redact_pii,
            ))
        
        # Execute IMAP routing
        if not self.settings.dry_run:
            if decision.final_status == WorkflowStatus.UNCERTAIN:
                return self._route_uncertain(
                    candidate, mailbox, imap, parsed, fingerprint, record, decision, draft_path, started
                )
            elif rule.action == "needs_llm":
                return self._route_llm(
                    candidate, mailbox, imap, parsed, fingerprint, record, decision, draft_path, started, latency_ms
                )
            else:
                return self._route_rule(
                    candidate, mailbox, imap, parsed, fingerprint, record, decision, draft_path, started, rule
                )
        
        # Dry run - just return simulated result
        duration_ms = int((perf_counter() - started) * 1000)
        return ProcessingResult(
            action_taken=f"simulate_{decision.action_taken}",
            final_status=decision.final_status,
            category=decision.category,
            confidence=decision.confidence,
            target_folder=decision.target_folder,
            draft_path=draft_path,
            latency_ms=duration_ms,
            error=None,
        )
    

    def _perform_imap_routing(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
        parsed: ParsedEmail,
        fingerprint: str,
        record: "EmailRecord",
        decision: FinalDecision,
        draft_path: str | None,
    ) -> str | None | ProcessingResult:
        """Perform IMAP flag setting, copy, and source deletion.

        Returns the target UID if successful, or a ProcessingResult if source cleanup fails.
        """
        # Set flags if needed
        if decision.flags:
            imap.set_flagged(mailbox.imap_source_folder, candidate.uid)

        # Copy message
        target_uid = imap.copy_message(
            mailbox.imap_source_folder,
            candidate.uid,
            decision.target_folder,
        )

        # Delete source
        try:
            imap.delete_message(mailbox.imap_source_folder, candidate.uid)
            return target_uid
        except Exception as exc:
            LOGGER.exception("Copy succeeded but source cleanup failed")
            return self._handle_cleanup_failure(
                candidate, mailbox, parsed, fingerprint, record, decision, draft_path, target_uid, exc
            )

    def _log_and_return_success(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        parsed: ParsedEmail,
        fingerprint: str,
        decision: FinalDecision,
        draft_path: str | None,
        effective_action: str,
        duration_ms: int,
    ) -> ProcessingResult:
        """Log successful routing to audit and return ProcessingResult."""
        self.audit.log(
            level="INFO",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=parsed.message_id,
            fingerprint=fingerprint,
            imap_uid=candidate.uid,
            sender=parsed.sender,
            subject=parsed.subject,
            status_before="processing",
            status_after=decision.final_status.value,
            category=decision.category,
            confidence=decision.confidence,
            action_taken=effective_action,
            target_folder=decision.target_folder,
            draft_path=draft_path,
            duration_ms=duration_ms,
            error=None,
            dry_run=False,
        )
        
        return ProcessingResult(
            action_taken=effective_action,
            final_status=decision.final_status,
            category=decision.category,
            confidence=decision.confidence,
            target_folder=decision.target_folder,
            draft_path=draft_path,
            latency_ms=duration_ms,
            error=None,
        )

    def _handle_routing_error(
        self,
        record: "EmailRecord",
        decision: FinalDecision,
        draft_path: str | None,
        started: float,
        exc: Exception,
    ) -> ProcessingResult:
        """Handle generic exception during routing."""
        LOGGER.exception("Routing execution failed")
        duration_ms = int((perf_counter() - started) * 1000)
        self.state.mark_failed(record.id, error_message=str(exc), error_type=exc.__class__.__name__)
        return ProcessingResult(
            action_taken=ActionTaken.FAILED,
            final_status=WorkflowStatus.FAILED,
            category=None,
            confidence=None,
            target_folder=None,
            draft_path=draft_path,
            latency_ms=duration_ms,
            error=str(exc),
        )

    def _route_uncertain(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
        parsed: ParsedEmail,
        fingerprint: str,
        record: "EmailRecord",
        decision: FinalDecision,
        draft_path: str | None,
        started: float,
    ) -> ProcessingResult:
        """Route to uncertain folder for fallback."""
        try:
            target_uid_or_result = self._perform_imap_routing(
                candidate, mailbox, imap, parsed, fingerprint, record, decision, draft_path
            )
            if isinstance(target_uid_or_result, ProcessingResult):
                return target_uid_or_result
            
            target_uid = target_uid_or_result
            duration_ms = int((perf_counter() - started) * 1000)
            effective_action = f"move_{decision.action_taken}"
            
            self.state.mark_uncertain(
                record.id,
                category=decision.category,
                confidence=decision.confidence,
                target_folder=decision.target_folder,
                target_uid=target_uid,
                action_taken=effective_action,
            )
            
            return self._log_and_return_success(
                candidate, mailbox, parsed, fingerprint, decision, draft_path, effective_action, duration_ms
            )
        except Exception as exc:
            return self._handle_routing_error(record, decision, draft_path, started, exc)

    def _route_rule(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
        parsed: ParsedEmail,
        fingerprint: str,
        record: "EmailRecord",
        decision: FinalDecision,
        draft_path: str | None,
        started: float,
        rule: "RuleDecision",
    ) -> ProcessingResult:
        """Route based on evaluated rules."""
        try:
            target_uid_or_result = self._perform_imap_routing(
                candidate, mailbox, imap, parsed, fingerprint, record, decision, draft_path
            )
            if isinstance(target_uid_or_result, ProcessingResult):
                return target_uid_or_result

            target_uid = target_uid_or_result
            duration_ms = int((perf_counter() - started) * 1000)
            effective_action = f"move_{decision.action_taken}"

            self.state.mark_processed(
                record.id,
                category=decision.category,
                confidence=decision.confidence,
                target_folder=decision.target_folder,
                target_uid=target_uid,
                action_taken=effective_action,
                draft_path=draft_path,
                rule_hit=rule.reason,
                model_name=None,
                model_latency_ms=None,
            )
            
            return self._log_and_return_success(
                candidate, mailbox, parsed, fingerprint, decision, draft_path, effective_action, duration_ms
            )
        except Exception as exc:
            return self._handle_routing_error(record, decision, draft_path, started, exc)

    def _route_llm(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
        parsed: ParsedEmail,
        fingerprint: str,
        record: "EmailRecord",
        decision: FinalDecision,
        draft_path: str | None,
        started: float,
        llm_latency_ms: int | None,
    ) -> ProcessingResult:
        """Route based on LLM classification."""
        try:
            target_uid_or_result = self._perform_imap_routing(
                candidate, mailbox, imap, parsed, fingerprint, record, decision, draft_path
            )
            if isinstance(target_uid_or_result, ProcessingResult):
                return target_uid_or_result

            target_uid = target_uid_or_result
            duration_ms = int((perf_counter() - started) * 1000)
            effective_action = f"move_{decision.action_taken}"

            self.state.mark_processed(
                record.id,
                category=decision.category,
                confidence=decision.confidence,
                target_folder=decision.target_folder,
                target_uid=target_uid,
                action_taken=effective_action,
                draft_path=draft_path,
                rule_hit=None,
                model_name=self.settings.ollama_model,
                model_latency_ms=llm_latency_ms,
            )

            return self._log_and_return_success(
                candidate, mailbox, parsed, fingerprint, decision, draft_path, effective_action, duration_ms
            )
        except Exception as exc:
            return self._handle_routing_error(record, decision, draft_path, started, exc)
    
    def _handle_cleanup_failure(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        parsed: ParsedEmail,
        fingerprint: str,
        record: "EmailRecord",
        decision: FinalDecision,
        draft_path: str | None,
        target_uid: str | None,
        exc: Exception,
    ) -> ProcessingResult:
        """Handle failure to cleanup source after successful copy."""
        self.state.mark_move_cleanup_pending(
            record.id,
            category=decision.category,
            confidence=decision.confidence,
            target_folder=decision.target_folder,
            target_uid=target_uid,
            draft_path=draft_path,
            rule_hit=None,
            model_name=self.settings.ollama_model if decision.action_taken == "route_from_llm" else None,
            model_latency_ms=None,
            error_message=str(exc),
            error_type=exc.__class__.__name__,
        )
        
        # Audit log the cleanup failure
        self.audit.log(
            level="ERROR",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=parsed.message_id,
            fingerprint=fingerprint,
            imap_uid=candidate.uid,
            sender=parsed.sender,
            subject=parsed.subject,
            status_before="processing",
            status_after=WorkflowStatus.CLEANUP_PENDING.value,
            category=decision.category,
            confidence=decision.confidence,
            action_taken=MOVE_CLEANUP_PENDING_ACTION,
            target_folder=decision.target_folder,
            draft_path=draft_path,
            duration_ms=0,
            error=str(exc),
            dry_run=False,
        )
        
        return ProcessingResult(
            action_taken=MOVE_CLEANUP_PENDING_ACTION,
            final_status=WorkflowStatus.CLEANUP_PENDING,
            category=decision.category,
            confidence=decision.confidence,
            target_folder=decision.target_folder,
            draft_path=draft_path,
            latency_ms=0,
            error=str(exc),
        )
    
    def _handle_llm_circuit_breaker(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
        started: float,
        parsed: ParsedEmail,
        fingerprint: str,
        record: "EmailRecord",
        exc: CircuitBreakerOpenError,
    ) -> ProcessingResult:
        """Handle LLM circuit breaker open - route to uncertain."""
        LOGGER.warning("LLM circuit breaker is open, routing to uncertain: %s", exc)
        
        action_taken = f"{'simulate_' if self.settings.dry_run else 'move_'}route_uncertain_llm_failure"
        target_uid = None
        
        if not self.settings.dry_run:
            try:
                target_uid = imap.copy_message(
                    mailbox.imap_source_folder,
                    candidate.uid,
                    mailbox.imap_uncertain_folder,
                )
                imap.delete_message(mailbox.imap_source_folder, candidate.uid)
            except Exception as cleanup_exc:
                LOGGER.exception("LLM circuit breaker fallback cleanup failed")
                self.state.mark_move_cleanup_pending(
                    record.id,
                    category="other",
                    confidence=0.0,
                    target_folder=mailbox.imap_uncertain_folder,
                    target_uid=target_uid,
                    draft_path=None,
                    rule_hit=None,
                    model_name=self.settings.ollama_model,
                    model_latency_ms=None,
                    error_message=f"llm_circuit_breaker: {exc}; cleanup_failed: {cleanup_exc}",
                    error_type=cleanup_exc.__class__.__name__,
                )
                return ProcessingResult(
                    action_taken=MOVE_CLEANUP_PENDING_ACTION,
                    final_status=WorkflowStatus.CLEANUP_PENDING,
                    category="other",
                    confidence=0.0,
                    target_folder=mailbox.imap_uncertain_folder,
                    draft_path=None,
                    latency_ms=int((perf_counter() - started) * 1000),
                    error=str(cleanup_exc),
                )
            
            self.state.mark_uncertain(
                record.id,
                category="other",
                confidence=0.0,
                target_folder=mailbox.imap_uncertain_folder,
                target_uid=target_uid,
                action_taken=action_taken,
                error_message=f"llm_circuit_breaker: {exc}",
            )
        
        return ProcessingResult(
            action_taken=action_taken,
            final_status=WorkflowStatus.UNCERTAIN,
            category="other",
            confidence=0.0,
            target_folder=mailbox.imap_uncertain_folder,
            draft_path=None,
            latency_ms=int((perf_counter() - started) * 1000),
            error=str(exc),
        )
    
    def _handle_llm_failure(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        imap: IMAPClient,
        started: float,
        parsed: ParsedEmail,
        fingerprint: str,
        record: "EmailRecord",
        exc: RuntimeError,
    ) -> ProcessingResult:
        """Handle LLM failure - route to uncertain."""
        LOGGER.warning("LLM failed, routing to uncertain: %s", exc)
        
        action_taken = f"{'simulate_' if self.settings.dry_run else 'move_'}route_uncertain_llm_failure"
        target_uid = None
        
        if not self.settings.dry_run:
            try:
                target_uid = imap.copy_message(
                    mailbox.imap_source_folder,
                    candidate.uid,
                    mailbox.imap_uncertain_folder,
                )
                imap.delete_message(mailbox.imap_source_folder, candidate.uid)
            except Exception as cleanup_exc:
                LOGGER.exception("LLM failure fallback cleanup failed")
                self.state.mark_move_cleanup_pending(
                    record.id,
                    category="other",
                    confidence=0.0,
                    target_folder=mailbox.imap_uncertain_folder,
                    target_uid=target_uid,
                    draft_path=None,
                    rule_hit=None,
                    model_name=self.settings.ollama_model,
                    model_latency_ms=None,
                    error_message=f"llm_unavailable: {exc}; cleanup_failed: {cleanup_exc}",
                    error_type=cleanup_exc.__class__.__name__,
                )
                self.audit.log(
                    level="ERROR",
                    mailbox_id=mailbox.mailbox_id,
                    mailbox_user=mailbox.imap_user,
                    source_folder=mailbox.imap_source_folder,
                    message_id=parsed.message_id,
                    fingerprint=fingerprint,
                    imap_uid=candidate.uid,
                    sender=parsed.sender,
                    subject=parsed.subject,
                    status_before="processing",
                    status_after=WorkflowStatus.CLEANUP_PENDING.value,
                    category="other",
                    confidence=0.0,
                    action_taken=MOVE_CLEANUP_PENDING_ACTION,
                    target_folder=mailbox.imap_uncertain_folder,
                    duration_ms=int((perf_counter() - started) * 1000),
                    error=f"llm_unavailable: {exc}; cleanup_failed: {cleanup_exc}",
                    dry_run=False,
                )
                return ProcessingResult(
                    action_taken=MOVE_CLEANUP_PENDING_ACTION,
                    final_status=WorkflowStatus.CLEANUP_PENDING,
                    category="other",
                    confidence=0.0,
                    target_folder=mailbox.imap_uncertain_folder,
                    draft_path=None,
                    latency_ms=int((perf_counter() - started) * 1000),
                    error=str(cleanup_exc),
                )
            
            self.state.mark_uncertain(
                record.id,
                category="other",
                confidence=0.0,
                target_folder=mailbox.imap_uncertain_folder,
                target_uid=target_uid,
                action_taken=action_taken,
                error_message=f"llm_unavailable: {exc}",
            )
            self.audit.log(
                level="WARNING",
                mailbox_id=mailbox.mailbox_id,
                mailbox_user=mailbox.imap_user,
                source_folder=mailbox.imap_source_folder,
                message_id=parsed.message_id,
                fingerprint=fingerprint,
                imap_uid=candidate.uid,
                sender=parsed.sender,
                subject=parsed.subject,
                status_before="processing",
                status_after=WorkflowStatus.UNCERTAIN.value,
                category="other",
                confidence=0.0,
                action_taken=action_taken,
                target_folder=mailbox.imap_uncertain_folder,
                duration_ms=int((perf_counter() - started) * 1000),
                error=str(exc),
                dry_run=False,
            )
        
        return ProcessingResult(
            action_taken=action_taken,
            final_status=WorkflowStatus.UNCERTAIN,
            category="other",
            confidence=0.0,
            target_folder=mailbox.imap_uncertain_folder,
            draft_path=None,
            latency_ms=int((perf_counter() - started) * 1000),
            error=str(exc),
        )
    
    def _handle_classification_error(
        self,
        candidate: CandidateMessage,
        mailbox: MailboxConfig,
        started: float,
        parsed: ParsedEmail,
        fingerprint: str,
        record: "EmailRecord",
        exc: Exception,
    ) -> ProcessingResult:
        """Handle unexpected error during classification."""
        LOGGER.exception("Classification/routing failed for message")
        duration_ms = int((perf_counter() - started) * 1000)
        self.state.mark_failed(record.id, error_message=str(exc), error_type=exc.__class__.__name__)
        return ProcessingResult(
            action_taken=ActionTaken.FAILED,
            final_status=WorkflowStatus.FAILED,
            category=None,
            confidence=None,
            target_folder=None,
            draft_path=None,
            latency_ms=duration_ms,
            error=str(exc),
        )
    
    def _state_identity(self, value: str | None, *, redact: bool) -> str:
        """Redact PII if configured."""
        if not redact:
            return value or ""
        return "[redacted]" if value else ""
