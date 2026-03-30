from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .schemas import EmailRecord, LeaseAcquireResult, WorkerLockResult, WorkflowStatus
from .utils import _chmod_owner_only, _hash_value

MOVE_CLEANUP_PENDING_ACTION = "move_copy_succeeded_cleanup_pending"


class StateManager:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        _chmod_owner_only(self.db_path.parent)
        self._initialize()
        _chmod_owner_only(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS email_processing_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mailbox_id TEXT NOT NULL DEFAULT 'default',
                    message_id TEXT,
                    fingerprint TEXT NOT NULL,
                    content_fingerprint TEXT,
                    imap_uid TEXT,
                    uidvalidity TEXT,
                    source_folder TEXT,
                    target_folder TEXT,
                    target_uid TEXT,
                    sender TEXT,
                    sender_sha256 TEXT,
                    subject TEXT,
                    subject_sha256 TEXT,
                    internaldate TEXT,
                    status TEXT NOT NULL,
                    category TEXT,
                    confidence REAL,
                    action_taken TEXT,
                    draft_path TEXT,
                    error_message TEXT,
                    processing_started_at TEXT,
                    lock_expires_at TEXT,
                    lock_owner TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error_at TEXT,
                    last_error_type TEXT,
                    rule_hit TEXT,
                    model_name TEXT,
                    model_latency_ms INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_runtime_lock (
                    lock_name TEXT PRIMARY KEY,
                    lock_owner TEXT NOT NULL,
                    lock_expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(email_processing_state)").fetchall()}
            if "mailbox_id" not in columns:
                conn.execute("ALTER TABLE email_processing_state ADD COLUMN mailbox_id TEXT NOT NULL DEFAULT 'default'")
            if "content_fingerprint" not in columns:
                conn.execute("ALTER TABLE email_processing_state ADD COLUMN content_fingerprint TEXT")
            if "sender_sha256" not in columns:
                conn.execute("ALTER TABLE email_processing_state ADD COLUMN sender_sha256 TEXT")
            if "subject_sha256" not in columns:
                conn.execute("ALTER TABLE email_processing_state ADD COLUMN subject_sha256 TEXT")
            conn.execute("UPDATE email_processing_state SET mailbox_id = 'default' WHERE mailbox_id IS NULL OR mailbox_id = ''")
            conn.executescript(
                """
                DROP INDEX IF EXISTS idx_email_fingerprint;
                DROP INDEX IF EXISTS idx_email_message_id_not_null;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_email_mailbox_fingerprint
                    ON email_processing_state(mailbox_id, fingerprint);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_email_mailbox_message_id_not_null
                    ON email_processing_state(mailbox_id, message_id)
                    WHERE message_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_email_status
                    ON email_processing_state(status);
                CREATE INDEX IF NOT EXISTS idx_email_mailbox_status
                    ON email_processing_state(mailbox_id, status);
                CREATE INDEX IF NOT EXISTS idx_email_mailbox_content_fingerprint
                    ON email_processing_state(mailbox_id, content_fingerprint);
                """
            )

    def acquire_worker_lock(self, *, worker_id: str, lease_seconds: int) -> WorkerLockResult:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._connect() as conn:
            conn.execute("BEGIN EXCLUSIVE")
            row = conn.execute(
                "SELECT * FROM worker_runtime_lock WHERE lock_name = ?",
                ("main",),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO worker_runtime_lock (lock_name, lock_owner, lock_expires_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("main", worker_id, expires_at.isoformat(), now.isoformat()),
                )
                return WorkerLockResult(acquired=True, lock_owner=worker_id, reason="worker lock acquired")

            current_expires_at = datetime.fromisoformat(row["lock_expires_at"])
            if current_expires_at > now and row["lock_owner"] != worker_id:
                return WorkerLockResult(
                    acquired=False,
                    lock_owner=row["lock_owner"],
                    reason="another worker holds the active lock",
                )

            conn.execute(
                """
                UPDATE worker_runtime_lock
                SET lock_owner = ?, lock_expires_at = ?, updated_at = ?
                WHERE lock_name = ?
                """,
                (worker_id, expires_at.isoformat(), now.isoformat(), "main"),
            )
            return WorkerLockResult(acquired=True, lock_owner=worker_id, reason="worker lock refreshed")

    def release_worker_lock(self, *, worker_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM worker_runtime_lock WHERE lock_name = ? AND lock_owner = ?",
                ("main", worker_id),
            )

    def acquire_lease(
        self,
        *,
        mailbox_id: str,
        message_id: str | None,
        fingerprint: str,
        content_fingerprint: str | None = None,
        imap_uid: str,
        uidvalidity: str | None = None,
        sender: str,
        sender_sha256: str | None = None,
        subject: str,
        subject_sha256: str | None = None,
        source_folder: str,
        internaldate: str | None,
        worker_id: str,
        lease_seconds: int,
        max_retries: int,
    ) -> LeaseAcquireResult:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._connect() as conn:
            identity_rows = self._lookup_identity_rows(conn, mailbox_id, message_id, fingerprint, content_fingerprint)
            if self._is_identity_conflict(identity_rows):
                row = next((row for row in identity_rows if row is not None), None)
                assert row is not None
                return LeaseAcquireResult(
                    outcome="conflict",
                    record=self._row_to_record(row),
                    reason="message identity conflict",
                )

            row = next((row for row in identity_rows if row is not None), None)
            if row is not None:
                record = self._row_to_record(row)
                if self._is_uidvalidity_mismatch(record, uidvalidity):
                    return LeaseAcquireResult(outcome="conflict", record=record, reason="uidvalidity changed")
                if self._is_message_mismatch(record, message_id, fingerprint):
                    return LeaseAcquireResult(outcome="conflict", record=record, reason="message identity conflict")
                if record.status in {WorkflowStatus.PROCESSED, WorkflowStatus.SKIPPED, WorkflowStatus.UNCERTAIN}:
                    return LeaseAcquireResult(outcome="already_done", record=record, reason=f"message already {record.status.value}")
                if record.status == WorkflowStatus.CLEANUP_PENDING:
                    return LeaseAcquireResult(
                        outcome="already_done",
                        record=record,
                        reason="message copied already; source cleanup pending",
                    )
                if record.attempt_count >= max_retries and record.status in {WorkflowStatus.FAILED, WorkflowStatus.PROCESSING}:
                    return LeaseAcquireResult(outcome="already_done", record=record, reason="max retries exceeded")
                if record.status == WorkflowStatus.PROCESSING and record.lock_expires_at:
                    lock_expires_at = datetime.fromisoformat(record.lock_expires_at)
                    if lock_expires_at > now:
                        return LeaseAcquireResult(outcome="locked", record=record, reason="active lease exists")

                attempt_count = record.attempt_count + 1
                conn.execute(
                    """
                    UPDATE email_processing_state
                    SET status = ?, imap_uid = ?, uidvalidity = ?, source_folder = ?, sender = ?, sender_sha256 = ?,
                        subject = ?, subject_sha256 = ?, content_fingerprint = ?,
                        internaldate = ?, processing_started_at = ?, lock_expires_at = ?,
                        lock_owner = ?, attempt_count = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        WorkflowStatus.PROCESSING.value,
                        imap_uid,
                        uidvalidity,
                        source_folder,
                        sender,
                        sender_sha256 or _hash_value(sender),
                        subject,
                        subject_sha256 or _hash_value(subject),
                        content_fingerprint,
                        internaldate,
                        now.isoformat(),
                        expires_at.isoformat(),
                        worker_id,
                        attempt_count,
                        now.isoformat(),
                        record.id,
                    ),
                )
                updated_row = conn.execute("SELECT * FROM email_processing_state WHERE id = ?", (record.id,)).fetchone()
                return LeaseAcquireResult(
                    outcome="acquired",
                    record=self._row_to_record(updated_row),
                    reason="lease acquired",
                )

            cursor = conn.execute(
                """
                INSERT INTO email_processing_state (
                    mailbox_id, message_id, fingerprint, content_fingerprint, imap_uid, uidvalidity, source_folder,
                    sender, sender_sha256, subject, subject_sha256, internaldate,
                    status, processing_started_at, lock_expires_at, lock_owner, attempt_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mailbox_id,
                    message_id,
                    fingerprint,
                    content_fingerprint,
                    imap_uid,
                    uidvalidity,
                    source_folder,
                    sender,
                    sender_sha256 or _hash_value(sender),
                    subject,
                    subject_sha256 or _hash_value(subject),
                    internaldate,
                    WorkflowStatus.PROCESSING.value,
                    now.isoformat(),
                    expires_at.isoformat(),
                    worker_id,
                    1,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            inserted_row = conn.execute(
                "SELECT * FROM email_processing_state WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return LeaseAcquireResult(
                outcome="acquired",
                record=self._row_to_record(inserted_row),
                reason="new message acquired",
            )

    def mark_processed(
        self,
        record_id: int,
        *,
        category: str,
        confidence: float | None,
        target_folder: str,
        target_uid: str | None = None,
        action_taken: str,
        draft_path: str | None = None,
        rule_hit: str | None = None,
        model_name: str | None = None,
        model_latency_ms: int | None = None,
    ) -> None:
        self._update_terminal(
            record_id,
            status=WorkflowStatus.PROCESSED,
            category=category,
            confidence=confidence,
            target_folder=target_folder,
            target_uid=target_uid,
            action_taken=action_taken,
            draft_path=draft_path,
            error_message=None,
            last_error_type=None,
            rule_hit=rule_hit,
            model_name=model_name,
            model_latency_ms=model_latency_ms,
        )

    def mark_uncertain(
        self,
        record_id: int,
        *,
        category: str | None,
        confidence: float | None,
        target_folder: str | None = None,
        target_uid: str | None = None,
        action_taken: str = "route_uncertain",
        error_message: str | None = None,
    ) -> None:
        self._update_terminal(
            record_id,
            status=WorkflowStatus.UNCERTAIN,
            category=category,
            confidence=confidence,
            target_folder=target_folder,
            target_uid=target_uid,
            action_taken=action_taken,
            draft_path=None,
            error_message=error_message,
            last_error_type=None,
            rule_hit=None,
            model_name=None,
            model_latency_ms=None,
        )

    def mark_failed(self, record_id: int, *, error_message: str, error_type: str) -> None:
        self._update_terminal(
            record_id,
            status=WorkflowStatus.FAILED,
            category=None,
            confidence=None,
            target_folder=None,
            target_uid=None,
            action_taken="failed",
            draft_path=None,
            error_message=error_message,
            last_error_type=error_type,
            rule_hit=None,
            model_name=None,
            model_latency_ms=None,
        )

    def mark_move_cleanup_pending(
        self,
        record_id: int,
        *,
        category: str,
        confidence: float | None,
        target_folder: str,
        target_uid: str | None = None,
        draft_path: str | None = None,
        rule_hit: str | None = None,
        model_name: str | None = None,
        model_latency_ms: int | None = None,
        error_message: str,
        error_type: str,
    ) -> None:
        self._update_terminal(
            record_id,
            status=WorkflowStatus.CLEANUP_PENDING,
            category=category,
            confidence=confidence,
            target_folder=target_folder,
            target_uid=target_uid,
            action_taken=MOVE_CLEANUP_PENDING_ACTION,
            draft_path=draft_path,
            error_message=error_message,
            last_error_type=error_type,
            rule_hit=rule_hit,
            model_name=model_name,
            model_latency_ms=model_latency_ms,
        )

    def get_by_id(self, record_id: int) -> EmailRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM email_processing_state WHERE id = ?", (record_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_message_id(self, mailbox_id: str, message_id: str | None) -> EmailRecord | None:
        if message_id is None:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM email_processing_state WHERE mailbox_id = ? AND message_id = ?",
                (mailbox_id, message_id),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_fingerprint(self, mailbox_id: str, fingerprint: str) -> EmailRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM email_processing_state WHERE mailbox_id = ? AND fingerprint = ?",
                (mailbox_id, fingerprint),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_cleanup_candidates(self, *, mailbox_id: str, source_folder: str) -> list[EmailRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM email_processing_state
                WHERE mailbox_id = ?
                  AND source_folder = ?
                  AND target_folder IS NOT NULL
                  AND status = ?
                ORDER BY id
                """,
                (
                    mailbox_id,
                    source_folder,
                    WorkflowStatus.CLEANUP_PENDING.value,
                ),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def mark_cleanup_done(self, record_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE email_processing_state
                SET status = ?, action_taken = ?, error_message = NULL, last_error_at = NULL, last_error_type = NULL, updated_at = ?
                WHERE id = ?
                """,
                (WorkflowStatus.PROCESSED.value, "cleanup_source", now, record_id),
            )

    def _lookup_identity_rows(
        self,
        conn: sqlite3.Connection,
        mailbox_id: str,
        message_id: str | None,
        fingerprint: str,
        content_fingerprint: str | None,
    ) -> tuple[sqlite3.Row | None, sqlite3.Row | None, sqlite3.Row | None]:
        row_by_mid = None
        if message_id is not None:
            row_by_mid = conn.execute(
                "SELECT * FROM email_processing_state WHERE mailbox_id = ? AND message_id = ?",
                (mailbox_id, message_id),
            ).fetchone()
        row_by_fp = conn.execute(
            "SELECT * FROM email_processing_state WHERE mailbox_id = ? AND fingerprint = ?",
            (mailbox_id, fingerprint),
        ).fetchone()
        row_by_content = None
        if message_id is None and content_fingerprint:
            row_by_content = conn.execute(
                """
                SELECT *
                FROM email_processing_state
                WHERE mailbox_id = ? AND content_fingerprint = ? AND message_id IS NULL
                """,
                (mailbox_id, content_fingerprint),
            ).fetchone()
        return row_by_mid, row_by_fp, row_by_content

    def _is_identity_conflict(self, rows: tuple[sqlite3.Row | None, ...]) -> bool:
        ids = {row["id"] for row in rows if row is not None}
        return len(ids) > 1

    def _is_message_mismatch(self, record: EmailRecord, message_id: str | None, fingerprint: str) -> bool:
        return bool(message_id and record.message_id and record.message_id == message_id and record.fingerprint != fingerprint)

    def _is_uidvalidity_mismatch(self, record: EmailRecord, uidvalidity: str | None) -> bool:
        return bool(record.uidvalidity and uidvalidity and record.uidvalidity != uidvalidity)

    def _update_terminal(
        self,
        record_id: int,
        *,
        status: WorkflowStatus,
        category: str | None,
        confidence: float | None,
        target_folder: str | None,
        target_uid: str | None,
        action_taken: str,
        draft_path: str | None,
        error_message: str | None,
        last_error_type: str | None,
        rule_hit: str | None,
        model_name: str | None,
        model_latency_ms: int | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE email_processing_state
                SET status = ?, category = ?, confidence = ?, target_folder = ?, target_uid = ?, action_taken = ?,
                    draft_path = ?, error_message = ?, last_error_at = ?, last_error_type = ?,
                    processing_started_at = NULL, lock_expires_at = NULL, lock_owner = NULL,
                    rule_hit = ?, model_name = ?, model_latency_ms = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    category,
                    confidence,
                    target_folder,
                    target_uid,
                    action_taken,
                    draft_path,
                    error_message,
                    now if error_message else None,
                    last_error_type,
                    rule_hit,
                    model_name,
                    model_latency_ms,
                    now,
                    record_id,
                ),
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> EmailRecord:
        return EmailRecord.model_validate(dict(row))


