from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mail_ai_agent.constants import ActionTaken
from mail_ai_agent.state_manager import StateManager
from mail_ai_agent.uncertain_autoclose_cli import run_autoclose


def _set_updated_at(db_path: Path, record_id: int, timestamp: datetime) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE email_processing_state SET updated_at = ? WHERE id = ?",
            (timestamp.isoformat(), record_id),
        )
        conn.commit()


def test_run_autoclose_dry_run_selects_only_stale_exploration_uncertain(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    manager = StateManager(state_path)

    stale = manager.acquire_lease(
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
    assert stale.record is not None
    manager.mark_uncertain(
        stale.record.id,
        category="other",
        confidence=0.2,
        action_taken="move_route_uncertain_exploration",
        error_message="exploration",
    )
    _set_updated_at(state_path, stale.record.id, datetime.now(timezone.utc) - timedelta(hours=30))

    fresh = manager.acquire_lease(
        mailbox_id="kontakt",
        message_id="msg-2",
        fingerprint="fp-2",
        imap_uid="11",
        sender="client2@example.com",
        subject="Temat 2",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert fresh.record is not None
    manager.mark_uncertain(
        fresh.record.id,
        category="other",
        confidence=0.2,
        action_taken="move_route_uncertain_exploration",
        error_message="exploration",
    )

    payload = run_autoclose(
        state_db=state_path,
        audit_log=audit_path,
        older_than_hours=24,
        limit=100,
        dry_run=True,
        mailbox_id=None,
        redact_pii=True,
        fsync_audit=False,
    )

    assert payload["selected"] == 1
    assert payload["closed"] == 0
    assert payload["failed"] == 0
    assert payload["results"][0]["status"] == "would_close"

    stale_row = manager.get_by_id(stale.record.id)
    fresh_row = manager.get_by_id(fresh.record.id)
    assert stale_row is not None and stale_row.status.value == "uncertain"
    assert fresh_row is not None and fresh_row.status.value == "uncertain"
    assert not audit_path.exists()


def test_run_autoclose_closes_stale_exploration_uncertain(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    manager = StateManager(state_path)

    stale = manager.acquire_lease(
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
    assert stale.record is not None
    manager.mark_uncertain(
        stale.record.id,
        category="other",
        confidence=0.2,
        action_taken="move_route_uncertain_exploration",
        error_message="exploration",
    )
    _set_updated_at(state_path, stale.record.id, datetime.now(timezone.utc) - timedelta(hours=30))

    # Non-exploration uncertain should be ignored by autoclose.
    error_row = manager.acquire_lease(
        mailbox_id="kontakt",
        message_id="msg-2",
        fingerprint="fp-2",
        imap_uid="11",
        sender="client2@example.com",
        subject="Temat 2",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert error_row.record is not None
    manager.mark_uncertain(
        error_row.record.id,
        category="other",
        confidence=0.2,
        action_taken="move_route_uncertain_llm_failure",
        error_message="llm failed",
    )
    _set_updated_at(state_path, error_row.record.id, datetime.now(timezone.utc) - timedelta(hours=30))

    payload = run_autoclose(
        state_db=state_path,
        audit_log=audit_path,
        older_than_hours=24,
        limit=100,
        dry_run=False,
        mailbox_id=None,
        redact_pii=True,
        fsync_audit=False,
    )

    assert payload["selected"] == 1
    assert payload["closed"] == 1
    assert payload["failed"] == 0
    assert payload["results"][0]["status"] == "closed"

    stale_after = manager.get_by_id(stale.record.id)
    error_after = manager.get_by_id(error_row.record.id)
    assert stale_after is not None
    assert stale_after.status.value == "processed"
    assert stale_after.action_taken == ActionTaken.AUTO_CLOSE_UNCERTAIN_EXPLORATION.value
    assert error_after is not None and error_after.status.value == "uncertain"

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action_taken"] == ActionTaken.AUTO_CLOSE_UNCERTAIN_EXPLORATION.value
    assert entry["status_after"] == "processed"
