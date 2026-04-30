"""Centralized audit logging context manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING

from .constants import ActionTaken, WorkflowStatus

if TYPE_CHECKING:
    from .audit_logger import AuditLogger
    from .config import MailboxConfig
    from .schemas import CandidateMessage, ParsedEmail, ProcessingResult


@dataclass
class AuditContext:
    """Context manager for standardized audit logging.
    
    Usage:
        with AuditContext(audit_logger, mailbox, candidate) as ctx:
            result = process_message(candidate)
            ctx.with_result(result)
    """
    
    audit: "AuditLogger"
    mailbox: "MailboxConfig"
    candidate: "CandidateMessage"
    started: float = field(default_factory=perf_counter, init=False)
    result: "ProcessingResult | None" = field(default=None, init=False)
    
    def __enter__(self) -> "AuditContext":
        return self
    
    def __exit__(self, exc_type, exc, tb) -> None:
        """Log audit entry on context exit."""
        if self.result is not None:
            self._log_result()
        elif exc is not None:
            self._log_exception(exc)
    
    def with_result(self, result: "ProcessingResult") -> "AuditContext":
        """Attach processing result for logging."""
        self.result = result
        return self
    
    def _log_result(self) -> None:
        """Log successful processing result."""
        if self.result is None:
            return
        
        duration_ms = int((perf_counter() - self.started) * 1000)
        
        # Determine log level based on result status
        level = "ERROR" if self.result.final_status == WorkflowStatus.FAILED else "INFO"
        
        self.audit.log(
            level=level,
            mailbox_id=self.mailbox.mailbox_id,
            mailbox_user=self.mailbox.imap_user,
            source_folder=self.mailbox.imap_source_folder,
            message_id=self.candidate.message_id,
            fingerprint=self._get_fingerprint(),
            imap_uid=self.candidate.uid,
            status_before="new",
            status_after=self.result.final_status.value,
            action_taken=self.result.action_taken.value if isinstance(self.result.action_taken, ActionTaken) else str(self.result.action_taken),
            target_folder=self.result.target_folder,
            duration_ms=duration_ms,
            error=self.result.error,
            dry_run=False,
        )
    
    def _log_exception(self, exc: Exception) -> None:
        """Log exception that occurred during processing."""
        duration_ms = int((perf_counter() - self.started) * 1000)
        
        self.audit.log(
            level="ERROR",
            mailbox_id=self.mailbox.mailbox_id,
            mailbox_user=self.mailbox.imap_user,
            source_folder=self.mailbox.imap_source_folder,
            message_id=self.candidate.message_id,
            fingerprint=self._get_fingerprint(),
            imap_uid=self.candidate.uid,
            status_before="new",
            status_after=WorkflowStatus.FAILED.value,
            action_taken=ActionTaken.FAILED.value,
            duration_ms=duration_ms,
            error=str(exc),
            dry_run=False,
        )
    
    def _get_fingerprint(self) -> str:
        """Compute fingerprint from candidate raw bytes."""
        import hashlib
        return hashlib.sha256(self.candidate.raw_bytes).hexdigest()


@dataclass
class DryRunAuditContext:
    """Context manager for dry-run audit logging (with parsed email details)."""
    
    audit: "AuditLogger"
    mailbox: "MailboxConfig"
    candidate: "CandidateMessage"
    parsed: "ParsedEmail"
    started: float = field(default_factory=perf_counter, init=False)
    result: "ProcessingResult | None" = field(default=None, init=False)
    
    def __enter__(self) -> "DryRunAuditContext":
        return self
    
    def __exit__(self, exc_type, exc, tb) -> None:
        if self.result is not None:
            self._log_result()
    
    def with_result(self, result: "ProcessingResult") -> "DryRunAuditContext":
        self.result = result
        return self
    
    def _log_result(self) -> None:
        """Log dry-run result with full email details."""
        if self.result is None:
            return
        
        duration_ms = int((perf_counter() - self.started) * 1000)
        
        from .email_parser import compute_message_fingerprint
        
        self.audit.log(
            level="INFO",
            mailbox_id=self.mailbox.mailbox_id,
            mailbox_user=self.mailbox.imap_user,
            source_folder=self.mailbox.imap_source_folder,
            message_id=self.parsed.message_id,
            fingerprint=compute_message_fingerprint(self.parsed),
            imap_uid=self.candidate.uid,
            sender=self.parsed.sender,
            subject=self.parsed.subject,
            status_before=None,
            status_after="simulated",
            action_taken=self.result.action_taken.value if isinstance(self.result.action_taken, ActionTaken) else str(self.result.action_taken),
            target_folder=self.result.target_folder,
            duration_ms=duration_ms,
            error=self.result.error,
            dry_run=True,
        )
