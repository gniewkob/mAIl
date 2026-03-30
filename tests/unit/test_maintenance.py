from __future__ import annotations

import os
import time
from pathlib import Path

from mail_ai_agent.maintenance import maintain_sqlite, prune_drafts, rotate_audit_log
from mail_ai_agent.state_manager import StateManager


def test_rotate_audit_log_creates_archive(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("x" * 100, encoding="utf-8")

    result = rotate_audit_log(audit, max_bytes=10, backup_count=3)

    assert result.rotated is True
    assert result.archive_path is not None
    assert result.archive_path.exists()
    assert audit.read_text(encoding="utf-8") == ""


def test_prune_drafts_removes_old_files(tmp_path: Path) -> None:
    old_file = tmp_path / "old.json"
    old_file.write_text("{}", encoding="utf-8")
    old_time = time.time() - 3 * 24 * 3600
    os.utime(old_file, (old_time, old_time))

    new_file = tmp_path / "new.json"
    new_file.write_text("{}", encoding="utf-8")

    result = prune_drafts(tmp_path, older_than_days=1)

    assert result.removed == 1
    assert result.kept == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_rotated_archive_has_restricted_permissions(tmp_path):
    import os
    import stat

    from mail_ai_agent.maintenance import rotate_audit_log

    log = tmp_path / "audit.jsonl"
    log.write_text("x" * 200, encoding="utf-8")
    result = rotate_audit_log(log, max_bytes=100)
    assert result.rotated is True
    assert stat.S_IMODE(os.stat(result.archive_path).st_mode) == 0o600


def test_maintain_sqlite_runs_integrity_check(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite"
    manager = StateManager(db_path)
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

    maintenance = maintain_sqlite(db_path)

    assert maintenance["status"] == "ok"
    assert maintenance["integrity_check"] == "ok"
