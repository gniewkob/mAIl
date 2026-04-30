from __future__ import annotations

import hashlib
import logging
from time import perf_counter

from .audit_logger import AuditLogger
from .cleanup_manager import CleanupManager
from .config import MailboxConfig, Settings
from .draft_store import DraftStore
from .email_parser import compute_content_fingerprint, compute_message_fingerprint, parse_email
from .folder_mapper import target_folders
from .imap_client import IMAPAuthError, IMAPClient
from .llm_gateway import LLMGateway
from .message_processor import MessageProcessor, ProcessingResult
from .constants import ActionTaken
from .schemas import (
    LeaseAcquireResult,
    MailboxProcessingReport,
    ParsedEmail,
    ProcessingReport,
    WorkflowStatus,
)
from .state_manager import StateManager
from .utils import _hash_value

LOGGER = logging.getLogger(__name__)


def process_mailboxes(settings: Settings) -> ProcessingReport:
    """Process all configured mailboxes.
    
    This is the main entry point for the worker. It acquires a global worker lock,
    processes each mailbox sequentially, and handles errors gracefully.
    """
    import sys
    state = StateManager(settings.state_db_path)
    audit = AuditLogger(
        settings.audit_log_path,
        redact_pii=settings.audit_redact_pii,
        fsync=settings.audit_fsync,
    )
    drafts = DraftStore(settings.draft_dir)
    llm = LLMGateway(settings)
    report = ProcessingReport(worker_id=settings.worker_id, dry_run=settings.dry_run)
    
    # Acquire global worker lock
    worker_lock = state.acquire_worker_lock(
        worker_id=settings.worker_id,
        lease_seconds=settings.processing_lease_seconds,
    )
    if not worker_lock.acquired:
        LOGGER.warning("Worker lock not acquired: %s", worker_lock.reason)
        report.worker_lock_denied = True
        audit.log(
            level="WARNING",
            action_taken=ActionTaken.WORKER_LOCK_DENIED,
            status_before=None,
            status_after=None,
            error=worker_lock.reason,
            lock_owner=worker_lock.lock_owner,
            worker_id=settings.worker_id,
            duration_ms=0,
        )
        return report
    
    try:
        processor = MessageProcessor(settings, state, audit, drafts, llm)
        cleanup_manager = CleanupManager(state, audit, settings.worker_id)
        
        for mailbox in settings.load_mailboxes():
            _refresh_worker_lock(state=state, settings=settings)
            
            try:
                mailbox_report = _process_mailbox(
                    mailbox=mailbox,
                    settings=settings,
                    processor=processor,
                    cleanup_manager=cleanup_manager,
                )
            except IMAPAuthError as exc:
                LOGGER.critical("IMAP authentication failed for mailbox %s: %s", mailbox.mailbox_id, exc)
                audit.log(
                    level="CRITICAL",
                    mailbox_id=mailbox.mailbox_id,
                    mailbox_user=mailbox.imap_user,
                    source_folder=mailbox.imap_source_folder,
                    status_before=None,
                    status_after="imap_auth_failed",
                    action_taken=ActionTaken.IMAP_AUTH_FAILED,
                    error=str(exc),
                    dry_run=settings.dry_run,
                )
                mailbox_report = MailboxProcessingReport(
                    mailbox_id=mailbox.mailbox_id,
                    mailbox_user=mailbox.imap_user,
                    failed=1,
                    imap_auth_failed=True,
                )
            except Exception as exc:  # pragma: no cover - mailbox isolation guard
                LOGGER.exception("Mailbox processing failed before completion for %s", mailbox.mailbox_id)
                audit.log(
                    level="ERROR",
                    mailbox_id=mailbox.mailbox_id,
                    mailbox_user=mailbox.imap_user,
                    source_folder=mailbox.imap_source_folder,
                    status_before=None,
                    status_after="mailbox_failed",
                    action_taken=ActionTaken.MAILBOX_FAILED,
                    error=str(exc),
                    dry_run=settings.dry_run,
                )
                mailbox_report = MailboxProcessingReport(
                    mailbox_id=mailbox.mailbox_id,
                    mailbox_user=mailbox.imap_user,
                    failed=1,
                )
            
            _aggregate_report(report, mailbox_report)
    finally:
        state.release_worker_lock(worker_id=settings.worker_id)
    
    return report


def process_inbox(settings: Settings) -> ProcessingReport:
    """Alias for process_mailboxes for backward compatibility."""
    return process_mailboxes(settings)


def _refresh_worker_lock(*, state: StateManager, settings: Settings) -> None:
    """Refresh the worker lock during processing."""
    result = state.acquire_worker_lock(
        worker_id=settings.worker_id,
        lease_seconds=settings.processing_lease_seconds,
    )
    if not result.acquired:
        raise RuntimeError(f"Worker lock lost during processing: {result.reason}")


def _process_mailbox(
    *,
    mailbox: MailboxConfig,
    settings: Settings,
    processor: MessageProcessor,
    cleanup_manager: CleanupManager,
) -> MailboxProcessingReport:
    """Process a single mailbox."""
    report = MailboxProcessingReport(
        mailbox_id=mailbox.mailbox_id,
        mailbox_user=mailbox.imap_user,
    )
    
    with IMAPClient(mailbox) as imap:
        # Validate folder setup
        imap.validate_runtime_setup(
            source_folder=mailbox.imap_source_folder,
            target_folders=target_folders(mailbox),
            dry_run=settings.dry_run,
        )
        
        # Run cleanup pass for previously incomplete operations
        if not settings.dry_run:
            cleanup_processed, cleanup_failed, cleanup_mismatch = cleanup_manager.run_cleanup_pass(
                mailbox=mailbox,
                imap=imap,
            )
            report.cleanup_pass_processed = cleanup_processed
            report.cleanup_pass_failed = cleanup_failed
            report.cleanup_uidvalidity_mismatch = cleanup_mismatch
        
        # Fetch candidates
        candidates = imap.fetch_candidates(mailbox.imap_source_folder)
        report.candidates_seen = len(candidates)
        
        # Process each candidate
        for candidate in candidates:
            _refresh_worker_lock(state=processor.state, settings=settings)
            
            result = processor.process_candidate(candidate, mailbox, imap)
            _update_report_from_result(report, result)
            
            # Audit logging for dry_run simulated messages
            if settings.dry_run and result.action_taken.startswith("simulate_"):
                _log_simulated_result(candidate, mailbox, result, processor)
    
    return report


def _aggregate_report(report: ProcessingReport, mailbox_report: MailboxProcessingReport) -> None:
    """Aggregate mailbox report into global report."""
    report.mailbox_reports.append(mailbox_report)
    report.mailboxes_processed += 1
    report.candidates_seen += mailbox_report.candidates_seen
    report.acquired += mailbox_report.acquired
    report.processed += mailbox_report.processed
    report.uncertain += mailbox_report.uncertain
    report.simulated += mailbox_report.simulated
    report.cleanup_pending += mailbox_report.cleanup_pending
    report.cleanup_pass_processed += mailbox_report.cleanup_pass_processed
    report.cleanup_pass_failed += mailbox_report.cleanup_pass_failed
    report.cleanup_uidvalidity_mismatch += mailbox_report.cleanup_uidvalidity_mismatch
    report.failed += mailbox_report.failed
    report.skipped += mailbox_report.skipped
    report.conflicts += mailbox_report.conflicts
    if mailbox_report.imap_auth_failed:
        report.imap_auth_failures += 1


def _update_report_from_result(report: MailboxProcessingReport, result: ProcessingResult) -> None:
    """Update mailbox report based on processing result."""
    if result.action_taken.startswith("simulate_"):
        report.simulated += 1
    elif result.action_taken == "cleanup_source_already_done":
        report.skipped += 1
    elif result.action_taken == "cleanup_source_conflict_duplicate":
        report.conflicts += 1
    elif result.action_taken == "skip_conflict":
        report.conflicts += 1
    elif result.action_taken.startswith("skip_"):
        report.skipped += 1
    elif result.action_taken == "failed_parse":
        # Special case: parse failures count as failed in dry_run
        report.failed += 1
    elif result.action_taken == "failed":
        report.failed += 1
    elif result.final_status == WorkflowStatus.UNCERTAIN:
        report.uncertain += 1
    elif result.final_status == WorkflowStatus.PROCESSED:
        report.processed += 1
    elif result.final_status == WorkflowStatus.CLEANUP_PENDING:
        report.cleanup_pending += 1
        report.failed += 1


def _log_simulated_result(
    candidate: "CandidateMessage",
    mailbox: MailboxConfig,
    result: ProcessingResult,
    processor: MessageProcessor,
) -> None:
    """Log audit entry for dry run simulation."""
    # Re-parse to get the parsed email details
    try:
        parsed = parse_email(candidate.raw_bytes, processor.settings)
        processor.audit.log(
            level="INFO",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=parsed.message_id,
            fingerprint=compute_message_fingerprint(parsed),
            imap_uid=candidate.uid,
            sender=parsed.sender,
            subject=parsed.subject,
            status_before=None,
            status_after="simulated",
            category=result.category,
            confidence=result.confidence,
            action_taken=result.action_taken,
            target_folder=result.target_folder,
            draft_path=result.draft_path,
            duration_ms=result.latency_ms,
            error=result.error,
            dry_run=True,
        )
    except Exception:
        # If we can't parse, log with minimal info
        processor.audit.log(
            level="INFO",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=candidate.message_id,
            imap_uid=candidate.uid,
            status_before=None,
            status_after="simulated",
            action_taken=result.action_taken,
            duration_ms=result.latency_ms,
            error=result.error,
            dry_run=True,
        )


# Backward compatibility exports
__all__ = [
    "process_mailboxes",
    "process_inbox",
]
