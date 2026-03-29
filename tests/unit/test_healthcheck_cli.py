from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

from mail_ai_agent.healthcheck_cli import build_health_payload
from mail_ai_agent.state_manager import StateManager


def test_build_health_payload_ok_for_clean_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"

    manager = StateManager(state_path)
    lease = manager.acquire_lease(
        mailbox_id="inbox_a",
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
    audit_path.write_text(json.dumps({"status_after": "processed", "action_taken": "move_route_from_llm"}) + "\n", encoding="utf-8")
    stdout_path.write_text("ok\n", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    payload = build_health_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=60,
        max_uncertain=0,
    )

    assert payload["ok"] is True
    assert payload["issues"] == []
    assert payload["state"]["processed"] == 1


def test_build_health_payload_flags_recent_mailbox_failure_and_cleanup_pending(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"

    manager = StateManager(state_path)
    lease = manager.acquire_lease(
        mailbox_id="inbox_a",
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
    manager.mark_move_cleanup_pending(
        lease.record.id,
        category="question",
        confidence=0.5,
        target_folder="INBOX.Questions",
        error_message="delete failed",
        error_type="RuntimeError",
    )
    now_ts = datetime.now(timezone.utc).isoformat()
    audit_path.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": now_ts, "status_after": "mailbox_failed", "action_taken": "mailbox_failed", "error": "boom"}),
                json.dumps({"timestamp": now_ts, "status_after": "cleanup_pending", "action_taken": "cleanup_uidvalidity_mismatch", "error": "bad"}),
                json.dumps({"timestamp": now_ts, "status_after": "failed", "action_taken": "failed", "error": "Refusing folder-level expunge in INBOX.AI-Review"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_health_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=None,
        stderr_log=None,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=60,
        max_uncertain=0,
    )

    assert payload["ok"] is False
    issues = payload["issues"]
    assert any("state_cleanup_pending=1" in issue for issue in issues)
    assert "recent mailbox_failed present in audit log" in issues
    assert "recent cleanup_uidvalidity_mismatch present in audit log" in issues
    assert "recent folder-level expunge refusal present in audit log" in issues


def test_build_health_payload_ignores_old_audit_failures_outside_time_window(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

    manager = StateManager(state_path)
    lease = manager.acquire_lease(
        mailbox_id="inbox_a",
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
        json.dumps({"timestamp": old_ts, "status_after": "mailbox_failed", "action_taken": "mailbox_failed", "error": "boom"}) + "\n",
        encoding="utf-8",
    )

    payload = build_health_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=None,
        stderr_log=None,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=60,
        max_uncertain=0,
    )

    assert payload["ok"] is True
    assert payload["issues"] == []
