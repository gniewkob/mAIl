from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .constants import ActionTaken
from .schemas import WorkflowStatus

if TYPE_CHECKING:
    from .audit_logger import AuditLogger
    from .config import MailboxConfig
    from .imap_client import IMAPClient
    from .schemas import EmailRecord, LeaseAcquireResult
    from .state_manager import StateManager

LOGGER = logging.getLogger(__name__)


class SourceCleanupHandler:
    """Handles cleanup of source folder messages for already-processed emails.
    
    When a message is detected as already processed (via lease outcome), but still
    exists in the source folder, this handler attempts to clean it up.
    """
    
    def __init__(
        self,
        state: StateManager,
        audit: AuditLogger,
        worker_id: str,
    ) -> None:
        self.state = state
        self.audit = audit
        self.worker_id = worker_id
    
    def try_cleanup_already_processed(
        self,
        candidate: "CandidateMessage",
        mailbox: MailboxConfig,
        imap: IMAPClient,
        lease: LeaseAcquireResult,
        message_id: str | None,
        fingerprint: str,
        content_fingerprint: str | None,
        sender: str | None,
        subject: str | None,
    ) -> bool:
        """Try to cleanup source message if it was already processed.
        
        Returns True if cleanup was attempted (success or failure logged).
        """
        if lease.outcome != "already_done":
            return False
        
        record = lease.record
        if record is None:
            return False
        
        if record.status != WorkflowStatus.PROCESSED:
            return False
        
        if not record.target_folder:
            return False
        
        # Verify this is for the same source folder
        if record.source_folder and record.source_folder != mailbox.imap_source_folder:
            return False
        
        # Verify UIDVALIDITY matches
        if record.uidvalidity and candidate.uidvalidity and record.uidvalidity != candidate.uidvalidity:
            return False
        
        try:
            imap.delete_message(mailbox.imap_source_folder, candidate.uid)
        except Exception as exc:
            # Handle case where message is already gone
            if "deleted set is [], expected only" in str(exc):
                self._log_cleanup_already_done_missing(
                    candidate, mailbox, record, sender, subject, fingerprint
                )
                return True
            
            LOGGER.exception("Failed to clean already-processed source message")
            self._log_cleanup_already_done_failed(
                candidate, mailbox, record, sender, subject, fingerprint, exc
            )
            return True
        
        self._log_cleanup_already_done(
            candidate, mailbox, record, sender, subject, fingerprint
        )
        return True
    
    def try_cleanup_processed_conflict(
        self,
        candidate: "CandidateMessage",
        mailbox: MailboxConfig,
        imap: IMAPClient,
        lease: LeaseAcquireResult,
        message_id: str | None,
        fingerprint: str,
        content_fingerprint: str | None,
        sender: str,
        subject: str,
    ) -> bool:
        """Try to cleanup source message for conflict with processed record.
        
        This handles the case where message identity matches a processed record
        but there's a conflict (e.g., different UID).
        
        Returns True if cleanup was attempted.
        """
        if lease.outcome != "conflict":
            return False
        
        if lease.reason != "message identity conflict":
            return False
        
        # Find matching processed records
        matches = self.state.get_identity_matches(
            mailbox_id=mailbox.mailbox_id,
            message_id=message_id,
            fingerprint=fingerprint,
            content_fingerprint=content_fingerprint,
        )
        
        if not matches:
            return False
        
        from .utils import _hash_value
        sender_sha256 = _hash_value(sender)
        subject_sha256 = _hash_value(subject)
        
        for record in matches:
            # Verify record is processed and matches message details
            if record.status != WorkflowStatus.PROCESSED:
                return False
            if not record.target_folder:
                return False
            if record.source_folder and record.source_folder != mailbox.imap_source_folder:
                return False
            if record.uidvalidity and candidate.uidvalidity and record.uidvalidity != candidate.uidvalidity:
                return False
            if record.sender_sha256 and record.sender_sha256 != sender_sha256:
                return False
            if record.subject_sha256 and record.subject_sha256 != subject_sha256:
                return False
        
        # All checks passed - safe to cleanup
        try:
            imap.delete_message(mailbox.imap_source_folder, candidate.uid)
        except Exception as exc:
            LOGGER.exception("Failed to clean processed conflict duplicate")
            return True
        
        self._log_cleanup_conflict_duplicate(
            candidate, mailbox, sender, subject, fingerprint
        )
        return True
    
    def _log_cleanup_already_done(
        self,
        candidate: "CandidateMessage",
        mailbox: MailboxConfig,
        record: EmailRecord,
        sender: str | None,
        subject: str | None,
        fingerprint: str,
    ) -> None:
        """Log successful cleanup of already-processed message."""
        self.audit.log(
            level="INFO",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=record.message_id,
            fingerprint=fingerprint,
            imap_uid=candidate.uid,
            sender=sender,
            subject=subject,
            status_before=record.status.value,
            status_after=record.status.value,
            action_taken=ActionTaken.CLEANUP_SOURCE_ALREADY_DONE,
            target_folder=record.target_folder,
            target_uid=record.target_uid,
            error=None,
            dry_run=False,
        )
    
    def _log_cleanup_already_done_missing(
        self,
        candidate: "CandidateMessage",
        mailbox: MailboxConfig,
        record: EmailRecord,
        sender: str | None,
        subject: str | None,
        fingerprint: str,
    ) -> None:
        """Log cleanup of already-processed message that was already gone."""
        self.audit.log(
            level="INFO",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=record.message_id,
            fingerprint=fingerprint,
            imap_uid=candidate.uid,
            sender=sender,
            subject=subject,
            status_before=record.status.value,
            status_after=record.status.value,
            action_taken=ActionTaken.CLEANUP_SOURCE_ALREADY_DONE_MISSING,
            target_folder=record.target_folder,
            target_uid=record.target_uid,
            error=None,
            dry_run=False,
        )
    
    def _log_cleanup_already_done_failed(
        self,
        candidate: "CandidateMessage",
        mailbox: MailboxConfig,
        record: EmailRecord,
        sender: str | None,
        subject: str | None,
        fingerprint: str,
        exc: Exception,
    ) -> None:
        """Log failed cleanup of already-processed message."""
        self.audit.log(
            level="ERROR",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=record.message_id,
            fingerprint=fingerprint,
            imap_uid=candidate.uid,
            sender=sender,
            subject=subject,
            status_before=record.status.value,
            status_after=record.status.value,
            action_taken=ActionTaken.CLEANUP_SOURCE_ALREADY_DONE_FAILED,
            target_folder=record.target_folder,
            target_uid=record.target_uid,
            error=str(exc),
            dry_run=False,
        )
    
    def _log_cleanup_conflict_duplicate(
        self,
        candidate: "CandidateMessage",
        mailbox: MailboxConfig,
        sender: str,
        subject: str,
        fingerprint: str,
    ) -> None:
        """Log successful cleanup of conflict duplicate."""
        self.audit.log(
            level="INFO",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=None,
            fingerprint=fingerprint,
            imap_uid=candidate.uid,
            sender=sender,
            subject=subject,
            status_before=WorkflowStatus.PROCESSED.value,
            status_after=WorkflowStatus.PROCESSED.value,
            action_taken=ActionTaken.CLEANUP_SOURCE_CONFLICT_DUPLICATE,
            target_folder=None,
            error=None,
            dry_run=False,
        )
