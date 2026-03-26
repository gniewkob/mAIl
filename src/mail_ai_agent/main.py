from __future__ import annotations

import logging
from time import perf_counter

from .audit_logger import AuditLogger
from .config import MailboxConfig, Settings
from .decision_engine import decide_from_llm, decide_from_rule
from .draft_store import DraftStore
from .email_parser import compute_fingerprint, parse_email
from .imap_client import IMAPClient
from .llm_gateway import LLMGateway
from .rule_engine import evaluate_rules
from .schemas import MailboxProcessingReport, ProcessingReport
from .state_manager import StateManager


LOGGER = logging.getLogger(__name__)


def process_mailboxes(settings: Settings) -> ProcessingReport:
    logging.basicConfig(level=getattr(logging, settings.log_level))
    state = StateManager(settings.state_db_path)
    audit = AuditLogger(settings.audit_log_path)
    drafts = DraftStore(settings.draft_dir)
    llm = LLMGateway(settings)
    report = ProcessingReport(worker_id=settings.worker_id, dry_run=settings.dry_run)
    worker_lock = state.acquire_worker_lock(
        worker_id=settings.worker_id,
        lease_seconds=settings.processing_lease_seconds,
    )
    if not worker_lock.acquired:
        LOGGER.warning("Worker lock not acquired: %s", worker_lock.reason)
        report.worker_lock_denied = True
        audit.log(
            level="WARNING",
            action_taken="worker_lock_denied",
            status_before=None,
            status_after=None,
            error=worker_lock.reason,
            lock_owner=worker_lock.lock_owner,
            worker_id=settings.worker_id,
            duration_ms=0,
        )
        return report

    try:
        for mailbox in settings.load_mailboxes():
            mailbox_report = _process_mailbox(
                settings=settings,
                mailbox=mailbox,
                state=state,
                audit=audit,
                drafts=drafts,
                llm=llm,
            )
            report.mailbox_reports.append(mailbox_report)
            report.mailboxes_processed += 1
            report.candidates_seen += mailbox_report.candidates_seen
            report.acquired += mailbox_report.acquired
            report.processed += mailbox_report.processed
            report.uncertain += mailbox_report.uncertain
            report.failed += mailbox_report.failed
            report.skipped += mailbox_report.skipped
            report.conflicts += mailbox_report.conflicts
    finally:
        state.release_worker_lock(worker_id=settings.worker_id)
    return report


def process_inbox(settings: Settings) -> ProcessingReport:
    return process_mailboxes(settings)


def _process_mailbox(
    *,
    settings: Settings,
    mailbox: MailboxConfig,
    state: StateManager,
    audit: AuditLogger,
    drafts: DraftStore,
    llm: LLMGateway,
) -> MailboxProcessingReport:
    report = MailboxProcessingReport(mailbox_id=mailbox.mailbox_id, mailbox_user=mailbox.imap_user)
    with IMAPClient(mailbox) as imap:
        candidates = imap.fetch_candidates(mailbox.imap_source_folder)
        report.candidates_seen = len(candidates)
        for candidate in candidates:
            started = perf_counter()
            parsed = parse_email(candidate.raw_bytes, settings)
            fingerprint = compute_fingerprint(parsed)
            lease = state.acquire_lease(
                mailbox_id=mailbox.mailbox_id,
                message_id=parsed.message_id,
                fingerprint=fingerprint,
                imap_uid=candidate.uid,
                sender=parsed.sender,
                subject=parsed.subject,
                source_folder=mailbox.imap_source_folder,
                internaldate=candidate.internaldate,
                worker_id=settings.worker_id,
                lease_seconds=settings.processing_lease_seconds,
                max_retries=settings.max_retries,
            )
            if lease.outcome != "acquired":
                _log_skip(
                    audit=audit,
                    mailbox=mailbox,
                    parsed=parsed,
                    fingerprint=fingerprint,
                    candidate_uid=candidate.uid,
                    lease=lease,
                    dry_run=settings.dry_run,
                    duration_ms=int((perf_counter() - started) * 1000),
                )
                if lease.outcome == "conflict":
                    report.conflicts += 1
                else:
                    report.skipped += 1
                continue

            report.acquired += 1
            record = lease.record
            assert record is not None

            try:
                rule = evaluate_rules(parsed, mailbox)
                draft_path = None
                latency_ms = None
                if rule.action == "needs_llm":
                    classification, latency_ms = llm.classify(parsed)
                    decision = decide_from_llm(classification, settings, mailbox)
                else:
                    decision = decide_from_rule(rule)

                if decision.draft_reply:
                    draft_path = str(
                        drafts.save(
                            parsed_email=parsed,
                            decision=decision,
                            fingerprint=fingerprint,
                        )
                    )

                if not settings.dry_run:
                    if decision.flags:
                        imap.set_flagged(mailbox.imap_source_folder, candidate.uid)
                    imap.copy_message(mailbox.imap_source_folder, candidate.uid, decision.target_folder)

                if decision.final_status.value == "uncertain":
                    state.mark_uncertain(record.id, category=decision.category, confidence=decision.confidence)
                    report.uncertain += 1
                else:
                    state.mark_processed(
                        record.id,
                        category=decision.category,
                        confidence=decision.confidence,
                        target_folder=decision.target_folder,
                        action_taken=decision.action_taken,
                        draft_path=draft_path,
                        rule_hit=None if rule.action == "needs_llm" else rule.reason,
                        model_name=settings.ollama_model if rule.action == "needs_llm" else None,
                        model_latency_ms=latency_ms,
                    )
                    report.processed += 1

                audit.log(
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
                    action_taken=decision.action_taken,
                    target_folder=decision.target_folder,
                    draft_path=draft_path,
                    duration_ms=int((perf_counter() - started) * 1000),
                    error=None,
                    dry_run=settings.dry_run,
                )
            except Exception as exc:  # pragma: no cover - top-level safeguard
                LOGGER.exception("Failed to process message for mailbox %s", mailbox.mailbox_id)
                state.mark_failed(record.id, error_message=str(exc), error_type=exc.__class__.__name__)
                audit.log(
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
                    status_after="failed",
                    action_taken="failed",
                    duration_ms=int((perf_counter() - started) * 1000),
                    error=str(exc),
                    dry_run=settings.dry_run,
                )
                report.failed += 1
    return report


def _log_skip(
    *,
    audit: AuditLogger,
    mailbox: MailboxConfig,
    parsed,
    fingerprint: str,
    candidate_uid: str,
    lease,
    dry_run: bool,
    duration_ms: int,
) -> None:
    record = lease.record
    audit.log(
        level="INFO",
        mailbox_id=mailbox.mailbox_id,
        mailbox_user=mailbox.imap_user,
        source_folder=mailbox.imap_source_folder,
        message_id=parsed.message_id,
        fingerprint=fingerprint,
        imap_uid=candidate_uid,
        sender=parsed.sender,
        subject=parsed.subject,
        status_before=record.status.value if record else None,
        status_after=record.status.value if record else None,
        action_taken=f"skip_{lease.outcome}",
        error=lease.reason,
        dry_run=dry_run,
        duration_ms=duration_ms,
    )


if __name__ == "__main__":
    process_mailboxes(Settings())
