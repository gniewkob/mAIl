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
from mail_ai_agent.constants import ActionTaken
from mail_ai_agent.state_manager import StateManager


def test_audit_reporting_summary_and_csv(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                json.dumps({"mailbox_id": "inbox_a", "action_taken": "route_from_llm", "status_after": "processed", "category": "question"}),
                json.dumps({"mailbox_id": "inbox_b", "action_taken": ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING.value, "status_after": "cleanup_pending", "error": "boom"}),
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
    assert summary["actions"][ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING.value] == 1
    assert summary["statuses"]["processed"] == 2
    assert summary["mailboxes"]["inbox_a"] == 1
    assert summary["cleanup_pending"] == 2
    assert summary["simulated"] == 1
    assert summary["cleanup_pass_processed"] == 1
    assert summary["cleanup_uidvalidity_mismatch"] == 1
    assert csv_path.exists()


def test_audit_reporting_counts_all_cleanup_pending_statuses(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                json.dumps({"status_after": "cleanup_pending", "action_taken": ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING.value}),
                json.dumps({"status_after": "cleanup_pending", "action_taken": "cleanup_uidvalidity_mismatch"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = summarize_audit_records(load_audit_records(audit_path))

    assert summary["cleanup_pending"] == 2


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
    assert summary["uncertain_by_mailbox"] == {}
    assert summary["failed_by_mailbox"] == {}
    assert exported_rows == 2
    assert csv_path.exists()


def test_summarize_state_includes_uncertain_by_mailbox(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    lease_a = manager.acquire_lease(
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
    assert lease_a.record is not None
    manager.mark_uncertain(lease_a.record.id, category="other", confidence=0.2, error_message="manual review")

    lease_b = manager.acquire_lease(
        mailbox_id="inbox_b",
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
    assert lease_b.record is not None
    manager.mark_uncertain(lease_b.record.id, category="other", confidence=0.2, error_message="manual review")

    summary = summarize_state(tmp_path / "state.sqlite")

    assert summary["uncertain_by_mailbox"] == {"inbox_a": 1, "inbox_b": 1}
    assert summary["failed_by_mailbox"] == {}


def test_summarize_state_includes_failed_by_mailbox(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    lease = manager.acquire_lease(
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
    assert lease.record is not None
    manager.mark_failed(lease.record.id, error_message="boom", error_type="RuntimeError")

    summary = summarize_state(tmp_path / "state.sqlite")

    assert summary["failed_by_mailbox"] == {"inbox_a": 1}
    assert summary["uncertain_by_mailbox"] == {}


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


def test_load_audit_records_skips_malformed_lines(tmp_path: Path) -> None:
    from mail_ai_agent.reporting import load_audit_records

    log = tmp_path / "audit.jsonl"
    log.write_text(
        '{"timestamp": "2026-01-01T00:00:00+00:00", "action_taken": "ok"}\n'
        "NOT VALID JSON\n"
        '{"timestamp": "2026-01-01T00:01:00+00:00", "action_taken": "also_ok"}\n',
        encoding="utf-8",
    )
    records = load_audit_records(log)
    assert len(records) == 2
    assert records[0]["action_taken"] == "ok"
    assert records[1]["action_taken"] == "also_ok"


def test_tail_audit_records_skips_malformed_lines(tmp_path: Path) -> None:
    """tail_audit_records must skip malformed JSON lines."""
    from mail_ai_agent.reporting import tail_audit_records

    log_path = tmp_path / "audit.jsonl"
    log_path.write_text(
        '{"n": 1}\n' "NOT VALID JSON\n" '{"n": 2}\n',
        encoding="utf-8",
    )

    result = tail_audit_records(log_path, 5)
    assert len(result) == 2
    # Records are collected in reverse order from the end of the file, then reversed back.
    # {"n": 2} is last in file, so it's first in collected_lines.
    # reversed(collected_lines) restores the original file order for records.
    assert result[0]["n"] == 1
    assert result[1]["n"] == 2


def test_export_audit_csv_writes_atomically(tmp_path: Path) -> None:
    from mail_ai_agent.reporting import export_audit_csv

    dest = tmp_path / "out.csv"
    records = [{"action_taken": "move", "status_after": "processed"}]
    export_audit_csv(records, dest)
    assert dest.exists()
    assert not dest.with_suffix(".tmp").exists()
    content = dest.read_text(encoding="utf-8")
    assert "action_taken" in content
    assert "move" in content


def test_export_state_csv_writes_atomically(tmp_path: Path) -> None:
    from mail_ai_agent.reporting import export_state_csv
    from mail_ai_agent.state_manager import StateManager

    db = tmp_path / "state.sqlite"
    StateManager(db).acquire_lease(
        mailbox_id="mb", message_id="m1", fingerprint="fp1", imap_uid="1",
        sender="a@b.com", subject="Hi", source_folder="INBOX",
        internaldate=None, worker_id="w", lease_seconds=60, max_retries=3,
    )
    dest = tmp_path / "state.csv"
    count = export_state_csv(db, dest)
    assert count == 1
    assert dest.exists()
    assert not dest.with_suffix(".tmp").exists()
    assert "fingerprint" in dest.read_text(encoding="utf-8")


def test_summarize_state_closes_sqlite_connection(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    from mail_ai_agent import reporting

    state_db = tmp_path / "state.sqlite"
    with sqlite3.connect(state_db) as conn:
        conn.execute("CREATE TABLE email_processing_state (id INTEGER PRIMARY KEY, mailbox_id TEXT, status TEXT)")

    class TrackingConnection:
        def __init__(self, inner):
            self._inner = inner
            self.closed = False

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self._inner.__exit__(exc_type, exc, tb)

        def close(self) -> None:
            self.closed = True
            self._inner.close()

    holder: dict[str, TrackingConnection] = {}
    real_connect = reporting.sqlite3.connect

    def tracked_connect(path):
        wrapped = TrackingConnection(real_connect(path))
        holder["conn"] = wrapped
        return wrapped

    monkeypatch.setattr(reporting.sqlite3, "connect", tracked_connect)

    summary = reporting.summarize_state(state_db)

    assert summary["records"] == 0
    assert holder["conn"].closed is True
