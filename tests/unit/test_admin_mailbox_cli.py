from __future__ import annotations

from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.schemas import WorkflowStatus
from mail_ai_agent.state_manager import StateManager
from mail_ai_agent.admin_mailbox_cli import run_delete_imap_message, run_requeue_uncertain


class FakeAdminIMAPClient:
    copied: list[tuple[str, str, str]] = []
    deleted: list[tuple[str, str]] = []

    def __init__(self, mailbox) -> None:
        self.mailbox = mailbox

    def __enter__(self) -> "FakeAdminIMAPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    @classmethod
    def reset(cls) -> None:
        cls.copied = []
        cls.deleted = []

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> str:
        self.copied.append((source_folder, uid, target_folder))
        return "new-uid-1"

    def delete_message(self, folder: str, uid: str) -> None:
        self.deleted.append((folder, uid))


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
        MAILBOXES_CONFIG_PATH=None,
        STATE_DB_PATH=tmp_path / "state.sqlite",
        AUDIT_LOG_PATH=tmp_path / "audit.jsonl",
        OLLAMA_MODEL="qwen3:8b",
    )


def test_run_requeue_uncertain_moves_message_back_and_deletes_state(monkeypatch, tmp_path: Path) -> None:
    FakeAdminIMAPClient.reset()
    settings = _make_settings(tmp_path)
    state = StateManager(settings.state_db_path)
    acquired = state.acquire_lease(
        mailbox_id=settings.default_mailbox_id(),
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="4",
        sender="client@example.com",
        subject="Test",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert acquired.record is not None
    state.mark_uncertain(
        acquired.record.id,
        category="other",
        confidence=0.1,
        target_folder="INBOX.AI-Uncertain",
        target_uid="1",
        error_message="llm failed",
    )

    monkeypatch.setattr("mail_ai_agent.admin_mailbox_cli.IMAPClient", FakeAdminIMAPClient)

    payload = run_requeue_uncertain(settings=settings)

    assert payload["selected"] == 1
    assert payload["requeued"] == 1
    assert payload["failed"] == 0
    assert FakeAdminIMAPClient.copied == [("INBOX.AI-Uncertain", "1", "INBOX.AI-Review")]
    assert FakeAdminIMAPClient.deleted == [("INBOX.AI-Uncertain", "1")]
    assert state.list_by_status(status=WorkflowStatus.UNCERTAIN) == []


def test_run_delete_imap_message_deletes_requested_uid(monkeypatch, tmp_path: Path) -> None:
    FakeAdminIMAPClient.reset()
    settings = _make_settings(tmp_path)

    monkeypatch.setattr("mail_ai_agent.admin_mailbox_cli.IMAPClient", FakeAdminIMAPClient)

    payload = run_delete_imap_message(
        settings=settings,
        mailbox_id=settings.default_mailbox_id(),
        folder="INBOX",
        uid="35642",
    )

    assert payload["deleted"] is True
    assert FakeAdminIMAPClient.deleted == [("INBOX", "35642")]
