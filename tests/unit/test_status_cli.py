from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.state_manager import StateManager
from mail_ai_agent.status_cli import build_status_payload


def test_build_status_payload_summarizes_state_and_audit(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    manager = StateManager(state_path)

    processed = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="<processed@example.com>",
        fingerprint="fp-processed",
        content_fingerprint="cfp-processed",
        imap_uid="1",
        uidvalidity="999",
        sender="processed@example.com",
        subject="Processed",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert processed.record is not None
    manager.mark_processed(
        processed.record.id,
        category="question",
        confidence=0.9,
        action_taken="move_route_from_llm",
        target_folder="INBOX.Questions",
        draft_path=None,
    )

    cleanup = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="<cleanup@example.com>",
        fingerprint="fp-cleanup",
        content_fingerprint="cfp-cleanup",
        imap_uid="2",
        uidvalidity="999",
        sender="cleanup@example.com",
        subject="Cleanup",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert cleanup.record is not None
    manager.mark_move_cleanup_pending(
        cleanup.record.id,
        category="question",
        confidence=0.8,
        target_folder="INBOX.Questions",
        error_message="delete failed",
        error_type="RuntimeError",
    )

    audit_path.write_text(
        "\n".join(
            [
                json.dumps({"status_after": "simulated", "action_taken": "simulate_route_from_llm"}),
                json.dumps({"status_after": "processed", "action_taken": "cleanup_source"}),
                json.dumps({"status_after": "cleanup_pending", "action_taken": "cleanup_uidvalidity_mismatch"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_status_payload(state_db=state_path, audit_log=audit_path)

    assert payload["records"] == 2
    assert payload["processed"] == 1
    assert payload["failed"] == 0
    assert payload["uncertain"] == 0
    assert payload["cleanup_pending"] == 1
    assert payload["simulated"] == 1
    assert payload["cleanup_pass_processed"] == 1
    assert payload["cleanup_uidvalidity_mismatch"] == 1
