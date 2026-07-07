from __future__ import annotations

import unittest.mock as mock
from pathlib import Path
import pytest

from mail_ai_agent.main import process_mailboxes, process_inbox
from mail_ai_agent.config import Settings, MailboxConfig
from mail_ai_agent.schemas import WorkerLockResult, ProcessingReport, MailboxProcessingReport, WorkflowStatus, CandidateMessage
from mail_ai_agent.message_processor import ProcessingResult
from mail_ai_agent.constants import ActionTaken

@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
        DRY_RUN=True,
        STATE_DB_PATH=tmp_path / "state.sqlite",
        AUDIT_LOG_PATH=tmp_path / "audit.jsonl",
        DRAFT_DIR=tmp_path / "drafts",
        WORKER_ID="test-worker",
        PROCESSING_LEASE_SECONDS=60,
    )

@pytest.fixture
def mock_state():
    with mock.patch("mail_ai_agent.main.StateManager") as m:
        yield m.return_value

@pytest.fixture
def mock_audit():
    with mock.patch("mail_ai_agent.main.AuditLogger") as m:
        yield m.return_value

@pytest.fixture
def mock_imap():
    with mock.patch("mail_ai_agent.main.IMAPClient") as m:
        client = m.return_value.__enter__.return_value
        yield client

@pytest.fixture
def mock_llm():
    with mock.patch("mail_ai_agent.main.LLMGateway") as m:
        yield m.return_value

def test_process_mailboxes_lock_denied(settings, mock_state, mock_audit):
    # Setup: lock acquisition fails
    mock_state.acquire_worker_lock.return_value = WorkerLockResult(
        acquired=False, lock_owner="other-worker", reason="already held"
    )

    report = process_mailboxes(settings)

    assert report.worker_lock_denied is True
    mock_state.acquire_worker_lock.assert_called_once_with(
        worker_id=settings.worker_id,
        lease_seconds=settings.processing_lease_seconds
    )
    mock_audit.log.assert_called_once()
    assert mock_audit.log.call_args.kwargs["action_taken"] == ActionTaken.WORKER_LOCK_DENIED
    # Should not release lock if not acquired
    mock_state.release_worker_lock.assert_not_called()

def test_process_mailboxes_lock_acquired_and_released(settings, mock_state, mock_audit):
    # Setup: lock acquisition succeeds, no mailboxes
    mock_state.acquire_worker_lock.return_value = WorkerLockResult(
        acquired=True, lock_owner=settings.worker_id, reason="acquired"
    )
    with mock.patch("mail_ai_agent.config.Settings.load_mailboxes", return_value=[]):
        report = process_mailboxes(settings)

    assert report.worker_lock_denied is False
    mock_state.release_worker_lock.assert_called_once_with(worker_id=settings.worker_id)

def test_process_mailboxes_refresh_failure_in_mailbox_loop(settings, mock_state, mock_audit):
    # Setup: lock acquisition succeeds
    mock_state.acquire_worker_lock.side_effect = [
        WorkerLockResult(acquired=True, lock_owner=settings.worker_id, reason="acquired"),
        WorkerLockResult(acquired=False, lock_owner="other", reason="lost") # Refresh fails
    ]

    mailbox = MailboxConfig(mailbox_id="m1", imap_user="u1", imap_pass="p1", imap_host="h1")
    with mock.patch("mail_ai_agent.config.Settings.load_mailboxes", return_value=[mailbox]):
        with pytest.raises(RuntimeError, match="Worker lock lost during processing"):
            process_mailboxes(settings)

    # Ensure lock is still released in finally block
    mock_state.release_worker_lock.assert_called_once_with(worker_id=settings.worker_id)

def test_process_mailbox_refresh_failure_in_candidate_loop(settings, mock_state, mock_audit, mock_imap):
    # Setup
    # 1. process_mailboxes: acquire_worker_lock (initial)
    # 2. process_mailboxes loop: _refresh_worker_lock
    # 3. _process_mailbox candidate loop: _refresh_worker_lock (triggered by perf_counter)
    mock_state.acquire_worker_lock.side_effect = [
        WorkerLockResult(acquired=True, lock_owner=settings.worker_id, reason="acquired"),
        WorkerLockResult(acquired=True, lock_owner=settings.worker_id, reason="refreshed"),
        WorkerLockResult(acquired=False, lock_owner="other", reason="lost")
    ]

    mailbox = MailboxConfig(mailbox_id="m1", imap_user="u1", imap_pass="p1", imap_host="h1")
    mock_imap.fetch_candidates.return_value = [
        CandidateMessage(uid="1", uidvalidity="1", internaldate=None, raw_bytes=b"")
    ]

    # Mock perf_counter to trigger refresh
    with mock.patch("mail_ai_agent.main.perf_counter") as mock_perf:
        # First call in _process_mailbox (last_refresh = perf_counter())
        # Second call in candidate loop (if perf_counter() - last_refresh > refresh_interval)
        mock_perf.side_effect = [100.0, 200.0]

        with mock.patch("mail_ai_agent.config.Settings.load_mailboxes", return_value=[mailbox]):
            # The RuntimeError from _refresh_worker_lock inside _process_mailbox
            # is NOT caught by the inner try-except in process_mailboxes because it's not an IMAPAuthError.
            # Wait, it IS caught by the `except Exception` in process_mailboxes.
            report = process_mailboxes(settings)

    assert report.failed == 1
    assert report.mailbox_reports[0].failed == 1
    mock_state.release_worker_lock.assert_called_once_with(worker_id=settings.worker_id)

def test_process_mailboxes_handles_mailbox_exception_gracefully(settings, mock_state, mock_audit):
    # Setup: one mailbox fails with unexpected exception
    mock_state.acquire_worker_lock.return_value = WorkerLockResult(
        acquired=True, lock_owner=settings.worker_id, reason="acquired"
    )

    m1 = MailboxConfig(mailbox_id="m1", imap_user="u1", imap_pass="p1", imap_host="h1")
    m2 = MailboxConfig(mailbox_id="m2", imap_user="u2", imap_pass="p2", imap_host="h2")

    with mock.patch("mail_ai_agent.config.Settings.load_mailboxes", return_value=[m1, m2]):
        with mock.patch("mail_ai_agent.main._process_mailbox") as mock_process:
            mock_process.side_effect = [
                Exception("Unexpected crash"),
                MailboxProcessingReport(mailbox_id="m2", mailbox_user="u2", processed=1)
            ]

            report = process_mailboxes(settings)

    assert report.mailboxes_processed == 2
    assert report.processed == 1
    assert report.failed == 1

    # Verify audit log for the failed mailbox
    # Audit log is called once for failure, once for maybe simulated (but mock_process returned successful report for m2)
    # Actually _process_mailbox handles its own successful audit.
    # The crash in _process_mailbox is caught in process_mailboxes loop.

    failed_mailbox_audit = next(call for call in mock_audit.log.call_args_list if call.kwargs.get("action_taken") == ActionTaken.MAILBOX_FAILED)
    assert failed_mailbox_audit.kwargs["mailbox_id"] == "m1"
    assert "Unexpected crash" in failed_mailbox_audit.kwargs["error"]

def test_update_report_from_result_coverage(mock_audit):
    # Tests branches in _update_report_from_result that might be missed
    from mail_ai_agent.main import _update_report_from_result

    report = MailboxProcessingReport(mailbox_id="test", mailbox_user="user")

    def make_res(action, status):
        return ProcessingResult(
            action_taken=action,
            final_status=status,
            category=None,
            confidence=None,
            target_folder=None,
            draft_path=None,
            latency_ms=0
        )

    # skip_conflict
    _update_report_from_result(report, make_res("skip_conflict", WorkflowStatus.SKIPPED))
    assert report.conflicts == 1

    # skip_ (other)
    _update_report_from_result(report, make_res("skip_other", WorkflowStatus.SKIPPED))
    assert report.skipped == 1

    # failed (not failed_parse)
    _update_report_from_result(report, make_res("failed", WorkflowStatus.FAILED))
    assert report.failed == 1

    # cleanup_pending
    _update_report_from_result(report, make_res("move_copy_succeeded_cleanup_pending", WorkflowStatus.CLEANUP_PENDING))
    assert report.cleanup_pending == 1
    assert report.failed == 2 # 1 from previous + 1 from this

def test_log_simulated_result_exception_coverage(settings, mock_audit, mock_llm):
    from mail_ai_agent.main import _log_simulated_result
    from mail_ai_agent.message_processor import MessageProcessor

    processor = MessageProcessor(settings, mock.Mock(), mock_audit, mock.Mock(), mock_llm)
    candidate = CandidateMessage(uid="1", message_id="mid", raw_bytes=b"invalid")
    mailbox = MailboxConfig(mailbox_id="m1", imap_user="u1", imap_pass="p1", imap_host="h1")
    result = ProcessingResult(
        action_taken="simulate_route",
        final_status=WorkflowStatus.PROCESSED,
        category=None,
        confidence=None,
        target_folder=None,
        draft_path=None,
        latency_ms=0
    )

    with mock.patch("mail_ai_agent.main.parse_email", side_effect=Exception("parse error")):
        _log_simulated_result(candidate, mailbox, result, processor)

    # Verify it logged the minimal info
    mock_audit.log.assert_called_once()
    kwargs = mock_audit.log.call_args.kwargs
    assert kwargs["message_id"] == "mid"
    assert kwargs["imap_uid"] == "1"
    assert kwargs["status_after"] == "simulated"

def test_process_inbox_alias(settings, mock_state):
    mock_state.acquire_worker_lock.return_value = WorkerLockResult(
        acquired=False, lock_owner="other", reason="denied"
    )
    report = process_inbox(settings)
    assert report.worker_lock_denied is True
