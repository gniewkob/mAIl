from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .constants import ActionTaken
from .schemas import WorkflowStatus

if TYPE_CHECKING:
    from .audit_logger import AuditLogger
    from .config import MailboxConfig
    from .imap_client import IMAPClient
    from .schemas import EmailRecord
    from .state_manager import StateManager

LOGGER = logging.getLogger(__name__)


class CleanupManager:
    """Manages cleanup operations for pending messages.
    
    Responsibilities:
    - Run cleanup pass for messages with cleanup_pending status
    - Handle UIDVALIDITY mismatches
    - Coordinate with IMAP for actual deletion
    - Update state and audit trail
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
    
    def run_cleanup_pass(
        self,
        mailbox: MailboxConfig,
        imap: IMAPClient,
    ) -> tuple[int, int, int]:
        """Run cleanup pass for cleanup_pending messages.
        
        Returns:
            Tuple of (processed_count, failed_count, mismatch_count)
        """
        candidates = self.state.list_cleanup_candidates(
            mailbox_id=mailbox.mailbox_id,
            source_folder=mailbox.imap_source_folder,
        )
        if not candidates:
            return 0, 0, 0
        
        current_uidvalidity = imap.get_uidvalidity(mailbox.imap_source_folder)
        cleaned_records: list[tuple[int, str | None, str | None, str | None, str | None, str | None]] = []
        failed_count = 0
        mismatch_count = 0
        
        for record in candidates:
            if not record.imap_uid:
                continue
            
            # Acquire cleanup lock to prevent race conditions
            if not self.state.acquire_cleanup_lock(record.id, self.worker_id):
                LOGGER.debug("Skipping cleanup for record %d - already locked", record.id)
                continue
            
            # Check UIDVALIDITY
            if record.uidvalidity and current_uidvalidity and record.uidvalidity != current_uidvalidity:
                mismatch_count += 1
                self._log_uidvalidity_mismatch(record, mailbox, current_uidvalidity)
                continue
            
            # Attempt deletion
            try:
                imap.delete_message(mailbox.imap_source_folder, record.imap_uid)
                cleaned_records.append((
                    record.id,
                    record.message_id,
                    record.fingerprint,
                    record.sender,
                    record.subject,
                    record.target_folder,
                ))
            except Exception as exc:
                failed_count += 1
                self._log_cleanup_failure(record, mailbox, exc)
                LOGGER.exception("Cleanup pass failed for mailbox %s", mailbox.mailbox_id)
        
        # Mark cleaned records as done
        if cleaned_records:
            for record_id, message_id, fingerprint, sender, subject, target_folder in cleaned_records:
                self.state.mark_cleanup_done(record_id)
                self._log_cleanup_success(
                    record_id, message_id, fingerprint, sender, subject,
                    target_folder, mailbox
                )
        
        return len(cleaned_records), failed_count, mismatch_count
    
    def _log_uidvalidity_mismatch(
        self,
        record: EmailRecord,
        mailbox: MailboxConfig,
        current_uidvalidity: str | None,
    ) -> None:
        """Log UIDVALIDITY mismatch event."""
        self.audit.log(
            level="WARNING",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=record.message_id,
            fingerprint=record.fingerprint,
            imap_uid=record.imap_uid,
            sender=record.sender,
            subject=record.subject,
            status_before=WorkflowStatus.CLEANUP_PENDING.value,
            status_after=WorkflowStatus.CLEANUP_PENDING.value,
            category=record.category,
            confidence=record.confidence,
            action_taken=ActionTaken.CLEANUP_UIDVALIDITY_MISMATCH,
            target_folder=record.target_folder,
            draft_path=record.draft_path,
            error=f"stored uidvalidity={record.uidvalidity}, current uidvalidity={current_uidvalidity}",
            dry_run=False,
        )
        LOGGER.warning(
            "Skipping cleanup due to UIDVALIDITY mismatch for mailbox %s: stored=%s current=%s",
            mailbox.mailbox_id,
            record.uidvalidity,
            current_uidvalidity,
        )
    
    def _log_cleanup_failure(
        self,
        record: EmailRecord,
        mailbox: MailboxConfig,
        exc: Exception,
    ) -> None:
        """Log cleanup failure event."""
        self.audit.log(
            level="ERROR",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=record.message_id,
            fingerprint=record.fingerprint,
            imap_uid=record.imap_uid,
            sender=record.sender,
            subject=record.subject,
            status_before=WorkflowStatus.CLEANUP_PENDING.value,
            status_after=WorkflowStatus.CLEANUP_PENDING.value,
            category=record.category,
            confidence=record.confidence,
            action_taken=ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING,
            target_folder=record.target_folder,
            draft_path=record.draft_path,
            error=str(exc),
            dry_run=False,
        )
    
    def _log_cleanup_success(
        self,
        record_id: int,
        message_id: str | None,
        fingerprint: str,
        sender: str,
        subject: str,
        target_folder: str | None,
        mailbox: MailboxConfig,
    ) -> None:
        """Log successful cleanup."""
        self.audit.log(
            level="INFO",
            mailbox_id=mailbox.mailbox_id,
            mailbox_user=mailbox.imap_user,
            source_folder=mailbox.imap_source_folder,
            message_id=message_id,
            fingerprint=fingerprint,
            sender=sender,
            subject=subject,
            status_before=WorkflowStatus.CLEANUP_PENDING.value,
            status_after=WorkflowStatus.PROCESSED.value,
            action_taken=ActionTaken.CLEANUP_SOURCE,
            target_folder=target_folder,
            error=None,
            dry_run=False,
        )
