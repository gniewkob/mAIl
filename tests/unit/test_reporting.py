from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.reporting import (
    export_audit_csv,
    export_state_csv,
    load_audit_records,
    summarize_audit_records,
    summarize_state,
    tail_audit_records,
)
from mail_ai_agent.state_manager import MOVE_CLEANUP_PENDING_ACTION, StateManager


def test_audit_reporting_summary_and_csv(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                json.dumps({"mailbox_id": "inbox_a", "action_taken": "route_from_llm", "status_after": "processed", "category": "question"}),
                json.dumps({"mailbox_id": "inbox_b", "action_taken": MOVE_CLEANUP_PENDING_ACTION, "status_after": "cleanup_pending", "error": "boom"}),
                json.dumps({"mailbox_id": "inbox_c", "action_taken": "simulate_route_from_llm", "status_after": "simulated", "category": "question"}),
                json.dumps({"mailbox_id": "inbox_d", "action_taken": "cleanup_source", "status_after": "processed"}),
                json.dumps({"mailbox_id": "inbox_e", "action_taken": "cleanup_uidvalidity_mismatch", "status_after": "cleanup_pending", "error": "mismatch"}),
            ]
        ),
        encoding="utf-8",
    )

    records = load_audit_records(audit_path)
    summary = summarize_audit_records(records)
    csv_path = tmp_path / "audit.csv"
    export_audit_csv(records, csv_path)

    assert summary["records"] == 5
    assert summary["actions"][MOVE_CLEANUP_PENDING_ACTION] == 1
    assert summary["statuses"]["processed"] == 2
    assert summary["mailboxes"]["inbox_a"] == 1
    assert summary["cleanup_pending"] == 1
    assert summary["simulated"] == 1
    assert summary["cleanup_pass_processed"] == 1
    assert summary["cleanup_uidvalidity_mismatch"] == 1
    assert csv_path.exists()


def test_state_reporting_summary_and_csv(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    result = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert result.record is not None
    manager.mark_processed(
        result.record.id,
        category="question",
        confidence=0.8,
        target_folder="INBOX.Questions",
        action_taken="route_from_llm",
    )
    retry = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-2",
        fingerprint="fp-2",
        imap_uid="11",
        sender="client2@example.com",
        subject="Drugie pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert retry.record is not None
    manager.mark_move_cleanup_pending(
        retry.record.id,
        category="question",
        confidence=0.7,
        target_folder="INBOX.Questions",
        error_message="delete failed",
        error_type="RuntimeError",
    )

    summary = summarize_state(tmp_path / "state.sqlite")
    csv_path = tmp_path / "state.csv"
    exported_rows = export_state_csv(tmp_path / "state.sqlite", csv_path)

    assert summary["records"] == 2
    assert summary["statuses"]["processed"] == 1
    assert summary["statuses"]["cleanup_pending"] == 1
    assert summary["mailboxes"]["inbox_a"] == 2
    assert summary["cleanup_pending"] == 1
    assert exported_rows == 2
    assert csv_path.exists()


def test_tail_audit_records_reads_last_n_lines(tmp_path: Path) -> None:
    """tail_audit_records must return last N records without loading whole file."""
    log_path = tmp_path / "audit.jsonl"
    records = [{"timestamp": f"2024-01-01T00:00:0{i}Z", "n": i} for i in range(10)]
    log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    result = tail_audit_records(log_path, 3)
    assert len(result) == 3
    assert result[0]["n"] == 7
    assert result[1]["n"] == 8
    assert result[2]["n"] == 9


def test_tail_audit_records_returns_all_when_fewer_than_n(tmp_path: Path) -> None:
    """tail_audit_records returns all records when file has fewer than n lines."""
    log_path = tmp_path / "audit.jsonl"
    records = [{"n": i} for i in range(3)]
    log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    result = tail_audit_records(log_path, 10)
    assert len(result) == 3


def test_tail_audit_records_missing_file_returns_empty(tmp_path: Path) -> None:
    """tail_audit_records returns [] for non-existent file."""
    result = tail_audit_records(tmp_path / "missing.jsonl", 5)
    assert result == []
