from __future__ import annotations

import os
import time
from pathlib import Path
import json

from mail_ai_agent.maintenance import maintain_sqlite, prune_drafts, rotate_audit_log, scrub_draft_pii, scrub_state_pii
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


def test_scrub_state_pii_issues_single_update(tmp_path, monkeypatch):
    """scrub_state_pii should issue a batch UPDATE, not N individual updates."""
    import sqlite3
    from mail_ai_agent.state_manager import StateManager
    from mail_ai_agent.maintenance import scrub_state_pii

    db_path = tmp_path / "state.sqlite"
    sm = StateManager(db_path)
    for i in range(3):
        sm.acquire_lease(
            mailbox_id="test",
            message_id=f"<msg{i}@test.com>",
            fingerprint=f"fp{i}",
            imap_uid=str(i),
            sender=f"sender{i}@example.com",
            subject=f"Subject {i}",
            source_folder="INBOX",
            internaldate=None,
            worker_id="w",
            lease_seconds=60,
            max_retries=3,
        )

    execute_calls: list = []
    original_connect = sqlite3.connect

    class TrackingConnection:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def execute(self, sql: str, *a, **k):
            if sql.strip().upper().startswith("UPDATE"):
                execute_calls.append(sql)
            return self._conn.execute(sql, *a, **k)

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, *args):
            return self._conn.__exit__(*args)

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

        def __setattr__(self, name: str, value) -> None:
            if name == "_conn":
                object.__setattr__(self, name, value)
            else:
                setattr(self._conn, name, value)

    def counting_connect(*args, **kwargs):
        return TrackingConnection(original_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", counting_connect)
    scrub_state_pii(db_path)

    update_count = len([s for s in execute_calls if "email_processing_state" in s])
    assert update_count == 1, f"Expected 1 batch UPDATE, got {update_count} UPDATE calls"


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


def test_scrub_state_pii_redacts_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite"
    manager = StateManager(db_path)
    result = manager.acquire_lease(
        mailbox_id="inbox_a",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Poufny temat",
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
        confidence=0.9,
        target_folder="INBOX.Questions",
        action_taken="move_route_from_llm",
    )

    scrub = scrub_state_pii(db_path)
    redacted = manager.get_by_id(result.record.id)

    assert scrub.updated_rows == 1
    assert redacted is not None
    assert redacted.sender == "[redacted]"
    assert redacted.subject == "[redacted]"
    assert redacted.sender_sha256 is not None
    assert redacted.subject_sha256 is not None


def test_rotate_audit_log_uses_rename_not_copy(tmp_path, monkeypatch):
    """rotate_audit_log must rename original (atomic) — not copy+truncate."""
    import shutil
    from mail_ai_agent.maintenance import rotate_audit_log

    log_path = tmp_path / "audit.jsonl"
    log_path.write_text("x" * 200, encoding="utf-8")

    copy2_called = []
    original_copy2 = shutil.copy2

    def tracking_copy2(*args, **kwargs):
        copy2_called.append(args)
        return original_copy2(*args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", tracking_copy2)
    result = rotate_audit_log(log_path, max_bytes=100)

    assert result.rotated is True
    assert not copy2_called, "rotate_audit_log must use rename (Path.replace), not shutil.copy2"
    archive = log_path.with_suffix(".jsonl.1")
    assert archive.exists()
    assert archive.read_text() == "x" * 200
    assert log_path.exists()
    assert log_path.read_text() == ""


def test_scrub_state_pii_preserves_sha256_hashes(tmp_path):
    """scrub_state_pii must compute sender/subject sha256 for rows with NULL hashes before wiping PII."""
    import hashlib
    import sqlite3
    from mail_ai_agent.state_manager import StateManager
    from mail_ai_agent.maintenance import scrub_state_pii

    db_path = tmp_path / "state.sqlite"
    # Initialise schema by creating StateManager
    StateManager(db_path)

    # Insert a row directly with NULL sha256 fields (simulating rows created by older code)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO email_processing_state
                (mailbox_id, message_id, fingerprint, imap_uid, source_folder,
                 sender, subject, status, attempt_count, lock_owner,
                 sender_sha256, subject_sha256, created_at, updated_at, lock_expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("test", "<m@test>", "fp1", "1", "INBOX",
             "alice@example.com", "Hello World", "pending", 0, "w",
             None, None,  # <-- intentionally NULL hashes
             "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00", "2024-01-02T00:00:00+00:00"),
        )

    scrub_state_pii(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM email_processing_state WHERE fingerprint = 'fp1'").fetchone()

    assert row["sender"] == "[redacted]", f"sender not redacted: {row['sender']}"
    assert row["subject"] == "[redacted]", f"subject not redacted: {row['subject']}"

    expected_sender_hash = hashlib.sha256("alice@example.com".encode()).hexdigest()
    expected_subject_hash = hashlib.sha256("Hello World".encode()).hexdigest()
    assert row["sender_sha256"] == expected_sender_hash, (
        f"sender_sha256 should be computed before wipe, got: {row['sender_sha256']}"
    )
    assert row["subject_sha256"] == expected_subject_hash, (
        f"subject_sha256 should be computed before wipe, got: {row['subject_sha256']}"
    )


def test_scrub_draft_pii_uses_atomic_write(tmp_path, monkeypatch):
    """scrub_draft_pii must write via tmp+os.replace, not direct write_text on the original file."""
    import json
    import os
    from pathlib import Path
    from mail_ai_agent.maintenance import scrub_draft_pii

    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir()
    draft_file = draft_dir / "test.json"
    draft_file.write_text(json.dumps({
        "sender": "alice@example.com",
        "subject": "Hello",
        "draft_reply": "Hi",
    }), encoding="utf-8")

    write_text_calls = []
    original_write_text = Path.write_text

    def tracking_write_text(self, *args, **kwargs):
        write_text_calls.append(str(self))
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", tracking_write_text)
    scrub_draft_pii(draft_dir)

    payload = json.loads(draft_file.read_text())
    assert payload["sender"] == "[redacted]"
    assert "sender_sha256" in payload

    direct_writes_to_original = [p for p in write_text_calls if p == str(draft_file)]
    assert not direct_writes_to_original, (
        "scrub_draft_pii must not write directly to the original draft file — use tmp+os.replace"
    )


def test_scrub_state_pii_is_idempotent(tmp_path: Path) -> None:
    """Running scrub_state_pii twice must not corrupt hashes and must return 0 updated_rows on second run."""
    import sqlite3 as _sqlite3

    db_path = tmp_path / "state.sqlite"
    conn = _sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE email_processing_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mailbox_id TEXT NOT NULL DEFAULT 'default',
            message_id TEXT,
            fingerprint TEXT NOT NULL,
            sender TEXT,
            sender_sha256 TEXT,
            subject TEXT,
            subject_sha256 TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO email_processing_state (fingerprint, sender, subject, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
        ("fp-idempotency", "sender@example.com", "Test subject", "processed"),
    )
    conn.commit()
    conn.close()

    result1 = scrub_state_pii(db_path)
    assert result1.updated_rows == 1

    conn = _sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT sender, sender_sha256, subject_sha256 FROM email_processing_state WHERE fingerprint = 'fp-idempotency'"
    ).fetchone()
    conn.close()
    assert row[0] == "[redacted]"
    assert row[1] is not None and len(row[1]) == 64
    first_hash = row[1]

    result2 = scrub_state_pii(db_path)
    assert result2.updated_rows == 0

    conn = _sqlite3.connect(db_path)
    row2 = conn.execute(
        "SELECT sender_sha256 FROM email_processing_state WHERE fingerprint = 'fp-idempotency'"
    ).fetchone()
    conn.close()
    assert row2[0] == first_hash, "Hash must not change on second run"


def test_scrub_draft_pii_redacts_sender_and_subject(tmp_path: Path) -> None:
    draft = tmp_path / "draft.json"
    draft.write_text(
        json.dumps(
            {
                "subject": "Poufny temat",
                "sender": "client@example.com",
                "draft_reply": "Treść draftu",
                "summary": "Podsumowanie",
                "category": "question",
            }
        ),
        encoding="utf-8",
    )

    result = scrub_draft_pii(tmp_path)
    payload = json.loads(draft.read_text(encoding="utf-8"))

    assert result.updated_files == 1
    assert payload["sender"] == "[redacted]"
    assert payload["subject"] == "[redacted]"
    assert payload["sender_sha256"]
    assert payload["subject_sha256"]
    assert payload["draft_reply"] == "Treść draftu"
