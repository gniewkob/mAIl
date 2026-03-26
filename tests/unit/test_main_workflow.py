from __future__ import annotations

import json
from email.message import EmailMessage
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.schemas import CandidateMessage, LLMClassification, ParsedEmail
from mail_ai_agent.state_manager import MOVE_CLEANUP_PENDING_ACTION, StateManager


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
        DRY_RUN=True,
        STATE_DB_PATH=tmp_path / "state.sqlite",
        AUDIT_LOG_PATH=tmp_path / "audit.jsonl",
        DRAFT_DIR=tmp_path / "drafts",
        WORKER_ID="test-worker",
    )


class FakeIMAPClient:
    copied: list[tuple[str, str, str]] = []
    flagged: list[tuple[str, str]] = []
    deleted: list[tuple[str, str]] = []
    expunged: list[str] = []

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def __enter__(self) -> "FakeIMAPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
        message = EmailMessage()
        message["From"] = "client@example.com"
        message["Subject"] = "Pytanie o cenę"
        message["Message-ID"] = "<test-1@example.com>"
        message.set_content("Jaka jest cena usługi manicure?")
        return [CandidateMessage(uid="42", uidvalidity="999", internaldate=None, raw_bytes=message.as_bytes())]

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> None:
        self.copied.append((source_folder, uid, target_folder))

    def set_flagged(self, folder: str, uid: str) -> None:
        self.flagged.append((folder, uid))

    def mark_deleted(self, folder: str, uid: str) -> None:
        self.deleted.append((folder, uid))

    def expunge(self, folder: str) -> None:
        self.expunged.append(folder)

    def get_uidvalidity(self, folder: str) -> str | None:
        return "999"


class FakeMultiMailboxIMAPClient:
    copied: list[tuple[str, str, str]] = []

    def __init__(self, mailbox) -> None:
        self.mailbox = mailbox

    def __enter__(self) -> "FakeMultiMailboxIMAPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
        message = EmailMessage()
        message["From"] = f"client+{self.mailbox.mailbox_id}@example.com"
        message["Subject"] = f"Pytanie {self.mailbox.mailbox_id}"
        message["Message-ID"] = f"<{self.mailbox.mailbox_id}@example.com>"
        message.set_content("Jaka jest cena usługi manicure?")
        return [CandidateMessage(uid=self.mailbox.mailbox_id, uidvalidity="999", internaldate=None, raw_bytes=message.as_bytes())]

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> None:
        self.copied.append((source_folder, uid, target_folder))

    def set_flagged(self, folder: str, uid: str) -> None:
        return None

    def mark_deleted(self, folder: str, uid: str) -> None:
        return None

    def expunge(self, folder: str) -> None:
        return None


class FakeFailingCleanupIMAPClient(FakeIMAPClient):
    def mark_deleted(self, folder: str, uid: str) -> None:
        self.deleted.append((folder, uid))
        raise RuntimeError("delete failed")


class FakeUidValidityMismatchIMAPClient(FakeIMAPClient):
    def get_uidvalidity(self, folder: str) -> str | None:
        return "1000"


class FakeEndToEndIMAPClient(FakeIMAPClient):
    def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
        complaint = EmailMessage()
        complaint["From"] = "angry@example.com"
        complaint["Subject"] = "Reklamacja po usłudze"
        complaint["Message-ID"] = "<complaint-1@example.com>"
        complaint.set_content("Składam reklamację, efekt usługi jest inny niż oczekiwany.")

        question = EmailMessage()
        question["From"] = "client@example.com"
        question["Subject"] = "Pytanie o cenę"
        question["Message-ID"] = "<question-1@example.com>"
        question.set_content("Jaka jest cena usługi manicure?")

        return [
            CandidateMessage(uid="41", uidvalidity="999", internaldate=None, raw_bytes=complaint.as_bytes()),
            CandidateMessage(uid="42", uidvalidity="999", internaldate=None, raw_bytes=question.as_bytes()),
        ]


class FakeLLMGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def classify(self, parsed_email: ParsedEmail) -> tuple[LLMClassification, int]:
        return (
            LLMClassification.model_validate(
                {
                    "category": "question",
                    "priority": "medium",
                    "requires_reply": True,
                    "confidence": 0.91,
                    "summary": "Klient pyta o cenę usługi.",
                    "entities": {},
                    "draft_reply": "Dzień dobry, cena zależy od zakresu usługi.",
                    "reasoning_short": "Treść jest prostym pytaniem ofertowym.",
                }
            ),
            12,
        )


class FakeEndToEndLLMGateway(FakeLLMGateway):
    def classify(self, parsed_email: ParsedEmail) -> tuple[LLMClassification, int]:
        assert parsed_email.message_id == "<question-1@example.com>"
        return super().classify(parsed_email)


def test_process_inbox_dry_run_does_not_persist_state_or_drafts(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path)

    report = process_inbox(settings)

    assert FakeIMAPClient.copied == []
    assert FakeIMAPClient.flagged == []
    assert FakeIMAPClient.deleted == []
    assert FakeIMAPClient.expunged == []
    assert report.candidates_seen == 1
    assert report.mailboxes_processed == 1
    assert len(report.mailbox_reports) == 1
    assert report.acquired == 0
    assert report.simulated == 1
    assert report.processed == 0
    assert report.failed == 0
    audit_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    payload = json.loads(audit_lines[0])
    assert payload["mailbox_id"] == settings.default_mailbox_id()
    assert payload["status_after"] == "simulated"
    assert payload["dry_run"] is True
    manager = StateManager(tmp_path / "state.sqlite")
    assert manager.get_by_message_id(settings.default_mailbox_id(), "<test-1@example.com>") is None
    draft_files = list((tmp_path / "drafts").glob("*.json"))
    assert len(draft_files) == 0


def test_process_inbox_moves_message_when_not_in_dry_run(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    FakeIMAPClient.copied = []
    FakeIMAPClient.flagged = []
    FakeIMAPClient.deleted = []
    FakeIMAPClient.expunged = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path)
    settings = settings.model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.processed == 1
    assert FakeIMAPClient.copied == [("INBOX.AI-Review", "42", "INBOX.Questions")]
    assert FakeIMAPClient.deleted == [("INBOX.AI-Review", "42")]
    assert FakeIMAPClient.expunged == ["INBOX.AI-Review"]
    audit_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(audit_lines[0])
    assert payload["action_taken"] == "move_route_from_llm"


def test_process_inbox_marks_cleanup_pending_when_delete_fails(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    FakeFailingCleanupIMAPClient.copied = []
    FakeFailingCleanupIMAPClient.flagged = []
    FakeFailingCleanupIMAPClient.deleted = []
    FakeFailingCleanupIMAPClient.expunged = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeFailingCleanupIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path)
    settings = settings.model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.processed == 0
    assert report.failed == 1
    assert FakeFailingCleanupIMAPClient.copied == [("INBOX.AI-Review", "42", "INBOX.Questions")]
    assert FakeFailingCleanupIMAPClient.deleted == [("INBOX.AI-Review", "42")]
    assert FakeFailingCleanupIMAPClient.expunged == []

    manager = StateManager(tmp_path / "state.sqlite")
    record = manager.get_by_message_id(settings.default_mailbox_id(), "<test-1@example.com>")
    assert record is not None
    assert record.status.value == "cleanup_pending"
    assert record.uidvalidity == "999"
    assert record.target_folder == "INBOX.Questions"
    assert record.action_taken == MOVE_CLEANUP_PENDING_ACTION

    retry = manager.acquire_lease(
        mailbox_id=settings.default_mailbox_id(),
        message_id="<test-1@example.com>",
        fingerprint=record.fingerprint,
        imap_uid="42",
        sender="client@example.com",
        subject="Pytanie o cenę",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="retry-worker",
        lease_seconds=60,
        max_retries=3,
    )
    assert retry.outcome == "already_done"
    assert retry.reason == "message copied already; source cleanup pending"

    audit_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(audit_lines[0])
    assert payload["action_taken"] == MOVE_CLEANUP_PENDING_ACTION


def test_process_inbox_runs_cleanup_pass_before_processing(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    manager = StateManager(tmp_path / "state.sqlite")
    acquired = manager.acquire_lease(
        mailbox_id="user_example_com",
        message_id="cleanup-msg@example.com",
        fingerprint="cleanup-fp",
        imap_uid="40",
        sender="old@example.com",
        subject="Stare cleanup pending",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert acquired.record is not None
    manager.mark_move_cleanup_pending(
        acquired.record.id,
        category="question",
        confidence=0.7,
        target_folder="INBOX.Questions",
        error_message="delete failed",
        error_type="RuntimeError",
    )

    FakeIMAPClient.copied = []
    FakeIMAPClient.flagged = []
    FakeIMAPClient.deleted = []
    FakeIMAPClient.expunged = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path).model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.processed == 1
    assert report.cleanup_pass_processed == 1
    assert report.cleanup_pass_failed == 0
    assert report.cleanup_uidvalidity_mismatch == 0
    assert FakeIMAPClient.deleted == [("INBOX.AI-Review", "40"), ("INBOX.AI-Review", "42")]
    assert FakeIMAPClient.expunged == ["INBOX.AI-Review", "INBOX.AI-Review"]
    cleaned = manager.get_by_message_id(settings.default_mailbox_id(), "cleanup-msg@example.com")
    assert cleaned is not None
    assert cleaned.status.value == "processed"
    assert cleaned.action_taken == "cleanup_source"


def test_process_inbox_skips_cleanup_when_uidvalidity_mismatches(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    manager = StateManager(tmp_path / "state.sqlite")
    acquired = manager.acquire_lease(
        mailbox_id="user_example_com",
        message_id="cleanup-msg@example.com",
        fingerprint="cleanup-fp",
        content_fingerprint="cleanup-content-fp",
        imap_uid="40",
        uidvalidity="999",
        sender="old@example.com",
        subject="Stare cleanup pending",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert acquired.record is not None
    manager.mark_move_cleanup_pending(
        acquired.record.id,
        category="question",
        confidence=0.7,
        target_folder="INBOX.Questions",
        error_message="delete failed",
        error_type="RuntimeError",
    )

    FakeUidValidityMismatchIMAPClient.copied = []
    FakeUidValidityMismatchIMAPClient.flagged = []
    FakeUidValidityMismatchIMAPClient.deleted = []
    FakeUidValidityMismatchIMAPClient.expunged = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeUidValidityMismatchIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path).model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.processed == 1
    assert report.cleanup_pass_processed == 0
    assert report.cleanup_uidvalidity_mismatch == 1
    assert FakeUidValidityMismatchIMAPClient.deleted == [("INBOX.AI-Review", "42")]
    assert FakeUidValidityMismatchIMAPClient.expunged == ["INBOX.AI-Review"]
    still_pending = manager.get_by_message_id(settings.default_mailbox_id(), "cleanup-msg@example.com")
    assert still_pending is not None
    assert still_pending.status.value == "cleanup_pending"
    assert still_pending.action_taken == MOVE_CLEANUP_PENDING_ACTION

    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    mismatch_entries = [line for line in audit_lines if line.get("action_taken") == "cleanup_uidvalidity_mismatch"]
    assert len(mismatch_entries) == 1


def test_process_inbox_end_to_end_persists_rule_and_llm_outputs(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    FakeEndToEndIMAPClient.copied = []
    FakeEndToEndIMAPClient.flagged = []
    FakeEndToEndIMAPClient.deleted = []
    FakeEndToEndIMAPClient.expunged = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeEndToEndIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeEndToEndLLMGateway)
    settings = make_settings(tmp_path)
    settings = settings.model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.candidates_seen == 2
    assert report.processed == 2
    assert report.failed == 0
    assert FakeEndToEndIMAPClient.copied == [
        ("INBOX.AI-Review", "41", "INBOX.Complaints"),
        ("INBOX.AI-Review", "42", "INBOX.Questions"),
    ]
    assert FakeEndToEndIMAPClient.flagged == [("INBOX.AI-Review", "41")]
    assert FakeEndToEndIMAPClient.deleted == [
        ("INBOX.AI-Review", "41"),
        ("INBOX.AI-Review", "42"),
    ]
    assert FakeEndToEndIMAPClient.expunged == [
        "INBOX.AI-Review",
        "INBOX.AI-Review",
    ]

    manager = StateManager(tmp_path / "state.sqlite")
    complaint = manager.get_by_message_id(settings.default_mailbox_id(), "<complaint-1@example.com>")
    question = manager.get_by_message_id(settings.default_mailbox_id(), "<question-1@example.com>")
    assert complaint is not None
    assert question is not None
    assert complaint.category == "complaint"
    assert complaint.action_taken == "move_skip_ai"
    assert complaint.target_folder == "INBOX.Complaints"
    assert complaint.uidvalidity == "999"
    assert complaint.rule_hit == "complaint pattern matched"
    assert complaint.model_name is None
    assert question.category == "question"
    assert question.action_taken == "move_route_from_llm"
    assert question.target_folder == "INBOX.Questions"
    assert question.uidvalidity == "999"
    assert question.model_name == settings.ollama_model
    assert question.model_latency_ms == 12
    assert question.draft_path is not None

    draft_path = Path(question.draft_path)
    assert draft_path.exists()
    draft_payload = json.loads(draft_path.read_text(encoding="utf-8"))
    assert draft_payload["category"] == "question"
    assert draft_payload["sender"] == "client@example.com"
    assert "cena" in draft_payload["draft_reply"].lower()

    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(audit_lines) == 2
    by_message_id = {line["message_id"]: line for line in audit_lines}
    assert by_message_id["<complaint-1@example.com>"]["action_taken"] == "move_skip_ai"
    assert by_message_id["<complaint-1@example.com>"]["category"] == "complaint"
    assert by_message_id["<question-1@example.com>"]["action_taken"] == "move_route_from_llm"
    assert by_message_id["<question-1@example.com>"]["draft_path"] == question.draft_path


def test_process_inbox_handles_multiple_mailboxes_sequentially(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    manifest = tmp_path / "mailboxes.json"
    manifest.write_text(
        json.dumps(
            {
                "mailboxes": [
                    {"mailbox_id": "kontakt", "imap_user": "kontakt@example.com", "imap_pass": "secret-a"},
                    {"mailbox_id": "shop", "imap_user": "shop@example.com", "imap_pass": "secret-b"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeMultiMailboxIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = Settings(
        IMAP_HOST="imap.example.com",
        STATE_DB_PATH=tmp_path / "state.sqlite",
        AUDIT_LOG_PATH=tmp_path / "audit.jsonl",
        DRAFT_DIR=tmp_path / "drafts",
        WORKER_ID="test-worker",
        MAILBOXES_CONFIG_PATH=manifest,
        DRY_RUN=True,
    )

    report = process_inbox(settings)

    assert report.mailboxes_processed == 2
    assert report.candidates_seen == 2
    assert report.simulated == 2
    assert report.processed == 0
    assert [entry.mailbox_id for entry in report.mailbox_reports] == ["kontakt", "shop"]
    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {line["mailbox_id"] for line in audit_lines} == {"kontakt", "shop"}
