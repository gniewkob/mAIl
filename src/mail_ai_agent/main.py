from __future__ import annotations

import hashlib
import logging
from time import perf_counter

from .audit_logger import AuditLogger
from .config import MailboxConfig, Settings
from .decision_engine import decide_from_llm, decide_from_rule
from .draft_store import DraftStore
from .email_parser import compute_content_fingerprint, compute_message_fingerprint, parse_email
from .imap_client import IMAPClient
from .llm_gateway import LLMGateway
from .rule_engine import evaluate_rules
from .schemas import MailboxProcessingReport, ProcessingReport, WorkflowStatus
from .state_manager import MOVE_CLEANUP_PENDING_ACTION, StateManager


LOGGER = logging.getLogger(__name__)


def process_mailboxes(settings: Settings) -> ProcessingReport:
    logging.basicConfig(level=getattr(logging, settings.log_level))
    state = StateManager(settings.state_db_path)
    audit = AuditLogger(settings.audit_log_path, redact_pii=settings.audit_redact_pii)
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
            _refresh_worker_lock(state=state, settings=settings)
            try:
                mailbox_report = _process_mailbox(
                    settings=settings,
                    mailbox=mailbox,
                    state=state,
                    audit=audit,
                    drafts=drafts,
                    llm=llm,
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
                    action_taken="mailbox_failed",
                    error=str(exc),
                    dry_run=settings.dry_run,
                )
                mailbox_report = MailboxProcessingReport(
                    mailbox_id=mailbox.mailbox_id,
                    mailbox_user=mailbox.imap_user,
                    failed=1,
                )
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
    finally:
        state.release_worker_lock(worker_id=settings.worker_id)
    return report


def process_inbox(settings: Settings) -> ProcessingReport:
    return process_mailboxes(settings)


def _effective_action_taken(action_taken: str, *, dry_run: bool) -> str:
    if dry_run:
        return f"simulate_{action_taken}"
    return f"move_{action_taken}"


def _hash_value(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _state_identity(value: str | None, *, redact: bool) -> str:
    if not redact:
        return value or ""
    return "[redacted]" if value else ""


def _refresh_worker_lock(*, state: StateManager, settings: Settings) -> None:
    result = state.acquire_worker_lock(
        worker_id=settings.worker_id,
        lease_seconds=settings.processing_lease_seconds,
    )
    if not result.acquired:
        raise RuntimeError(f"Worker lock lost during processing: {result.reason}")


def _target_folders(mailbox: MailboxConfig) -> list[str]:
    return [
        mailbox.imap_uncertain_folder,
        mailbox.imap_appointments_folder,
        mailbox.imap_questions_folder,
        mailbox.imap_complaints_folder,
        mailbox.imap_other_folder,
        mailbox.imap_billing_folder,
        mailbox.imap_system_folder,
    ]


def _run_cleanup_pass(
    *,
    mailbox: MailboxConfig,
    state: StateManager,
    audit: AuditLogger,
    imap: IMAPClient,
) -> tuple[int, int, int]:
    candidates = state.list_cleanup_candidates(mailbox_id=mailbox.mailbox_id, source_folder=mailbox.imap_source_folder)
    if not candidates:
        return 0, 0, 0

    current_uidvalidity = imap.get_uidvalidity(mailbox.imap_source_folder)
    cleaned_records: list[tuple[int, str | None, str | None, str | None, str | None, str | None]] = []
    failed_count = 0
    mismatch_count = 0
    for record in candidates:
        if not record.imap_uid:
            continue
        if record.uidvalidity and current_uidvalidity and record.uidvalidity != current_uidvalidity:
            mismatch_count += 1
            audit.log(
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
                action_taken="cleanup_uidvalidity_mismatch",
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
            continue
        try:
            imap.delete_message(mailbox.imap_source_folder, record.imap_uid)
            cleaned_records.append((record.id, record.message_id, record.fingerprint, record.sender, record.subject, record.target_folder))
        except Exception as exc:
            failed_count += 1
            audit.log(
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
                action_taken=MOVE_CLEANUP_PENDING_ACTION,
                target_folder=record.target_folder,
                draft_path=record.draft_path,
                error=str(exc),
                dry_run=False,
            )
            LOGGER.exception("Cleanup pass failed for mailbox %s", mailbox.mailbox_id)
    if cleaned_records:
        for record_id, message_id, fingerprint, sender, subject, target_folder in cleaned_records:
            state.mark_cleanup_done(record_id)
            audit.log(
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
                action_taken="cleanup_source",
                target_folder=target_folder,
                error=None,
                dry_run=False,
            )
    return len(cleaned_records), failed_count, mismatch_count


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
        imap.validate_routing_setup(
            source_folder=mailbox.imap_source_folder,
            target_folders=_target_folders(mailbox),
            dry_run=settings.dry_run,
        )
        if not settings.dry_run:
            cleanup_processed, cleanup_failed, cleanup_mismatch = _run_cleanup_pass(
                mailbox=mailbox,
                state=state,
                audit=audit,
                imap=imap,
            )
            report.cleanup_pass_processed += cleanup_processed
            report.cleanup_pass_failed += cleanup_failed
            report.cleanup_uidvalidity_mismatch += cleanup_mismatch
        candidates = imap.fetch_candidates(mailbox.imap_source_folder)
        report.candidates_seen = len(candidates)
        for candidate in candidates:
            _refresh_worker_lock(state=state, settings=settings)
            started = perf_counter()
            parsed = parse_email(candidate.raw_bytes, settings)
            fingerprint = compute_message_fingerprint(parsed)
            content_fingerprint = compute_content_fingerprint(parsed)
            target_uid: str | None = None
            if settings.dry_run:
                try:
                    rule = evaluate_rules(parsed, mailbox)
                    latency_ms = None
                    if rule.action == "needs_llm":
                        classification, latency_ms = llm.classify(parsed)
                        decision = decide_from_llm(classification, settings, mailbox)
                    else:
                        decision = decide_from_rule(rule)
                    report.simulated += 1
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
                        status_before=None,
                        status_after="simulated",
                        category=decision.category,
                        confidence=decision.confidence,
                        action_taken=_effective_action_taken(decision.action_taken, dry_run=True),
                        target_folder=decision.target_folder,
                        draft_path=None,
                        duration_ms=int((perf_counter() - started) * 1000),
                        error=None,
                        dry_run=True,
                        model_latency_ms=latency_ms,
                    )
                except Exception as exc:  # pragma: no cover - top-level safeguard
                    LOGGER.exception("Failed to simulate message for mailbox %s", mailbox.mailbox_id)
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
                        status_before=None,
                        status_after="simulated_failed",
                        action_taken="simulate_failed",
                        duration_ms=int((perf_counter() - started) * 1000),
                        error=str(exc),
                        dry_run=True,
                    )
                    report.failed += 1
                continue

            lease = state.acquire_lease(
                mailbox_id=mailbox.mailbox_id,
                message_id=parsed.message_id,
                fingerprint=fingerprint,
                content_fingerprint=content_fingerprint,
                imap_uid=candidate.uid,
                uidvalidity=candidate.uidvalidity,
                sender=_state_identity(parsed.sender, redact=settings.state_redact_pii),
                sender_sha256=_hash_value(parsed.sender),
                subject=_state_identity(parsed.subject, redact=settings.state_redact_pii),
                subject_sha256=_hash_value(parsed.subject),
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
                    try:
                        classification, latency_ms = llm.classify(parsed)
                    except Exception as exc:
                        if settings.llm_failure_route_to_uncertain:
                            action_taken = _effective_action_taken(
                                "route_uncertain_llm_failure",
                                dry_run=settings.dry_run,
                            )
                            if not settings.dry_run:
                                target_uid = imap.copy_message(
                                    mailbox.imap_source_folder,
                                    candidate.uid,
                                    mailbox.imap_uncertain_folder,
                                )
                                try:
                                    imap.delete_message(mailbox.imap_source_folder, candidate.uid)
                                except Exception as cleanup_exc:
                                    LOGGER.exception(
                                        "LLM failure fallback copied message but source cleanup failed for mailbox %s",
                                        mailbox.mailbox_id,
                                    )
                                    state.mark_move_cleanup_pending(
                                        record.id,
                                        category="other",
                                        confidence=0.0,
                                        target_folder=mailbox.imap_uncertain_folder,
                                        target_uid=target_uid,
                                        draft_path=None,
                                        rule_hit=None,
                                        model_name=settings.ollama_model,
                                        model_latency_ms=None,
                                        error_message=f"llm_unavailable: {exc}; cleanup_failed: {cleanup_exc}",
                                        error_type=cleanup_exc.__class__.__name__,
                                    )
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
                                        status_after=WorkflowStatus.CLEANUP_PENDING.value,
                                        category="other",
                                        confidence=0.0,
                                        action_taken=MOVE_CLEANUP_PENDING_ACTION,
                                        target_folder=mailbox.imap_uncertain_folder,
                                        duration_ms=int((perf_counter() - started) * 1000),
                                        error=f"llm_unavailable: {exc}; cleanup_failed: {cleanup_exc}",
                                        dry_run=settings.dry_run,
                                    )
                                    report.failed += 1
                                    report.cleanup_pending += 1
                                    continue
                            state.mark_uncertain(
                                record.id,
                                category="other",
                                confidence=0.0,
                                target_folder=mailbox.imap_uncertain_folder if not settings.dry_run else None,
                                target_uid=target_uid if not settings.dry_run else None,
                                action_taken=action_taken,
                                error_message=f"llm_unavailable: {exc}",
                            )
                            report.uncertain += 1
                            audit.log(
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
                                dry_run=settings.dry_run,
                            )
                            continue
                        raise
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

                action_taken = _effective_action_taken(decision.action_taken, dry_run=settings.dry_run)

                if not settings.dry_run:
                    if decision.flags:
                        imap.set_flagged(mailbox.imap_source_folder, candidate.uid)
                    target_uid = imap.copy_message(mailbox.imap_source_folder, candidate.uid, decision.target_folder)
                    try:
                        imap.delete_message(mailbox.imap_source_folder, candidate.uid)
                    except Exception as exc:
                        LOGGER.exception("Copy succeeded but source cleanup failed for mailbox %s", mailbox.mailbox_id)
                        state.mark_move_cleanup_pending(
                            record.id,
                            category=decision.category,
                            confidence=decision.confidence,
                            target_folder=decision.target_folder,
                            target_uid=target_uid,
                            draft_path=draft_path,
                            rule_hit=None if rule.action == "needs_llm" else rule.reason,
                            model_name=settings.ollama_model if rule.action == "needs_llm" else None,
                            model_latency_ms=latency_ms,
                            error_message=str(exc),
                            error_type=exc.__class__.__name__,
                        )
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
                            status_after=WorkflowStatus.CLEANUP_PENDING.value,
                            category=decision.category,
                            confidence=decision.confidence,
                            action_taken=MOVE_CLEANUP_PENDING_ACTION,
                            target_folder=decision.target_folder,
                            draft_path=draft_path,
                            duration_ms=int((perf_counter() - started) * 1000),
                            error=str(exc),
                            dry_run=settings.dry_run,
                        )
                        report.failed += 1
                        report.cleanup_pending += 1
                        continue

                if decision.final_status.value == "uncertain":
                    state.mark_uncertain(
                        record.id,
                        category=decision.category,
                        confidence=decision.confidence,
                        target_folder=decision.target_folder if not settings.dry_run else None,
                        target_uid=target_uid if not settings.dry_run else None,
                        action_taken=action_taken,
                    )
                    report.uncertain += 1
                else:
                    state.mark_processed(
                        record.id,
                        category=decision.category,
                        confidence=decision.confidence,
                        target_folder=decision.target_folder,
                        target_uid=target_uid if not settings.dry_run else None,
                        action_taken=action_taken,
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
                    action_taken=action_taken,
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
