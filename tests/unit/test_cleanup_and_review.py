from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.review_report import build_review_rows, export_review_csv, summarize_review_rows
from mail_ai_agent.state_manager import StateManager


def test_state_manager_lists_cleanup_candidates(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "state.sqlite")
    acquired = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="42",
        sender="client@example.com",
        subject="Pytanie",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert acquired.record is not None
    manager.mark_processed(
        acquired.record.id,
        category="question",
        confidence=0.9,
        target_folder="INBOX.Questions",
        action_taken="route_from_llm",
    )

    candidates = manager.list_cleanup_candidates(mailbox_id="inbox_a", source_folder="INBOX.AI-Review")

    assert len(candidates) == 1
    assert candidates[0].imap_uid == "42"
    manager.mark_cleanup_done(candidates[0].id)
    assert manager.list_cleanup_candidates(mailbox_id="inbox_a", source_folder="INBOX.AI-Review") == []


def test_review_report_builds_rows_and_csv(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-03-26T10:00:00Z",
                        "mailbox_id": "inbox_a",
                        "mailbox_user": "a@example.com",
                        "message_id": "m1",
                        "sender": "client@example.com",
                        "subject": "Pytanie",
                        "status_after": "processed",
                        "category": "question",
                        "confidence": 0.91,
                        "target_folder": "INBOX.Questions",
                        "action_taken": "route_from_llm",
                        "draft_path": "drafts/test.json",
                        "error": None,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-03-26T10:05:00Z",
                        "mailbox_id": "inbox_b",
                        "mailbox_user": "b@example.com",
                        "message_id": "m2",
                        "sender": "client2@example.com",
                        "subject": "Niejasne",
                        "status_after": "uncertain",
                        "category": "other",
                        "confidence": 0.51,
                        "target_folder": "INBOX.AI-Uncertain",
                        "action_taken": "route_uncertain",
                        "draft_path": None,
                        "error": None,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    rows = build_review_rows(audit_path)
    summary = summarize_review_rows(rows)
    csv_path = tmp_path / "review.csv"
    export_review_csv(rows, csv_path)

    assert len(rows) == 2
    assert rows[0]["mailbox_id"] == "inbox_a"
    assert summary["rows"] == 2
    assert summary["uncertain"] == 1
    assert summary["drafts"] == 1
    assert csv_path.exists()
