from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.metrics_exporter import build_metrics_payload
from mail_ai_agent.state_manager import StateManager


def test_build_metrics_payload_exports_health_and_quality_metrics(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"

    manager = StateManager(state_path)
    lease = manager.acquire_lease(
        mailbox_id="kontakt",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Temat",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease.record is not None
    manager.mark_processed(
        lease.record.id,
        category="question",
        confidence=0.9,
        target_folder="INBOX.Questions",
        action_taken="move_route_from_llm",
    )
    audit_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-03-29T12:00:00+00:00",
                "mailbox_id": "kontakt",
                "category": "question",
                "action_taken": "move_route_from_llm",
                "status_after": "processed",
                "target_folder": "INBOX.Questions",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stdout_path.write_text("ok\n", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    metrics = build_metrics_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=0,
    )

    assert "mailai_health_ok 1" in metrics
    assert "mailai_state_processed 1" in metrics
    assert 'mailai_mailbox_records{mailbox_id="kontakt"} 1' in metrics
    assert 'mailai_route_source_records{route_source="llm"} 1' in metrics
