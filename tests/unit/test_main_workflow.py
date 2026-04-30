from __future__ import annotations

import hashlib
import json
from email.message import EmailMessage
from pathlib import Path

from mail_ai_agent.config import MailboxConfig, Settings
from mail_ai_agent.email_parser import compute_content_fingerprint, compute_message_fingerprint
from mail_ai_agent.schemas import CandidateMessage, LLMClassification, ParsedEmail
from mail_ai_agent.constants import ActionTaken, MOVE_CLEANUP_PENDING_ACTION
from mail_ai_agent.state_manager import StateManager


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
    def __init__(self, mailbox: MailboxConfig) -> None:
        self.mailbox = mailbox
        # Reset and store per-instance lists on the class so assertions remain readable.
        type(self).copied = []
        type(self).flagged = []
        type(self).deleted = []
        type(self).expunged = []
        type(self).validated = []

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

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> str | None:
        self.copied.append((source_folder, uid, target_folder))
        return "142"

    def set_flagged(self, folder: str, uid: str) -> None:
        self.flagged.append((folder, uid))

    def delete_message(self, folder: str, uid: str) -> None:
        self.deleted.append((folder, uid))

    def get_uidvalidity(self, folder: str) -> str | None:
        return "999"

    def validate_runtime_setup(self, *, source_folder: str, target_folders: list[str], dry_run: bool) -> None:
        self.validated.append((source_folder, tuple(target_folders), dry_run))


class FakeMultiMailboxIMAPClient:
    def __init__(self, mailbox) -> None:
        self.mailbox = mailbox
        type(self).copied = []

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

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> str | None:
        self.copied.append((source_folder, uid, target_folder))

    def set_flagged(self, folder: str, uid: str) -> None:
        return None

    def delete_message(self, folder: str, uid: str) -> None:
        return None

    def get_uidvalidity(self, folder: str) -> str | None:
        return "99999"

    def validate_runtime_setup(self, *, source_folder: str, target_folders: list[str], dry_run: bool) -> None:
        return None


class FakeFailingCleanupIMAPClient(FakeIMAPClient):
    def delete_message(self, folder: str, uid: str) -> None:
        self.deleted.append((folder, uid))
        raise RuntimeError("delete failed")


class FakeUidValidityMismatchIMAPClient(FakeIMAPClient):
    def get_uidvalidity(self, folder: str) -> str | None:
        return "1000"


class FakePreflightFailingIMAPClient(FakeIMAPClient):
    def validate_runtime_setup(self, *, source_folder: str, target_folders: list[str], dry_run: bool) -> None:
        raise RuntimeError("missing target folder")


class FakeExpungeFailingCleanupPassIMAPClient(FakeIMAPClient):
    expunge_calls: int = 0

    def __init__(self, mailbox: MailboxConfig) -> None:
        super().__init__(mailbox)
        type(self).expunge_calls = 0

    def expunge(self, folder: str) -> None:
        self.expunged.append(folder)
        type(self).expunge_calls += 1
        if type(self).expunge_calls == 1:
            raise RuntimeError("expunge failed")


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


class FakeFailingLLMGateway(FakeLLMGateway):
    def classify(self, parsed_email: ParsedEmail) -> tuple[LLMClassification, int]:
        raise RuntimeError("ollama unavailable")


def test_process_inbox_dry_run_does_not_persist_state_or_drafts(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    FakeIMAPClient.copied = []
    FakeIMAPClient.flagged = []
    FakeIMAPClient.deleted = []
    FakeIMAPClient.validated = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path)

    report = process_inbox(settings)

    assert FakeIMAPClient.copied == []
    assert FakeIMAPClient.flagged == []
    assert FakeIMAPClient.deleted == []
    assert FakeIMAPClient.validated == [
        (
            "INBOX.AI-Review",
            (
                "INBOX.AI-Uncertain",
                "INBOX.Appointments",
                "INBOX.Questions",
                "INBOX.Complaints",
                "Junk",
                "INBOX.Newsletter",
                "INBOX.Offer",
                "INBOX.Other",
                "INBOX.Billing",
                "INBOX.System",
            ),
            True,
        )
    ]
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

    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path)
    settings = settings.model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.processed == 1
    assert FakeIMAPClient.copied == [("INBOX.AI-Review", "42", "INBOX.Questions")]
    assert FakeIMAPClient.deleted == [("INBOX.AI-Review", "42")]
    audit_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(audit_lines[0])
    assert payload["action_taken"] == "move_route_from_llm"


def test_process_inbox_marks_cleanup_pending_when_delete_fails(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeFailingCleanupIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path)
    settings = settings.model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.processed == 0
    assert report.failed == 1
    assert FakeFailingCleanupIMAPClient.copied == [("INBOX.AI-Review", "42", "INBOX.Questions")]
    assert FakeFailingCleanupIMAPClient.deleted == [("INBOX.AI-Review", "42")]

    manager = StateManager(tmp_path / "state.sqlite")
    record = manager.get_by_message_id(settings.default_mailbox_id(), "<test-1@example.com>")
    assert record is not None
    assert record.status.value == "cleanup_pending"
    assert record.uidvalidity == "999"
    assert record.target_folder == "INBOX.Questions"
    assert record.action_taken == ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING.value

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
    assert payload["action_taken"] == ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING.value


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

    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path).model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.processed == 1
    assert report.cleanup_pass_processed == 1
    assert report.cleanup_pass_failed == 0
    assert report.cleanup_uidvalidity_mismatch == 0
    assert FakeIMAPClient.deleted == [("INBOX.AI-Review", "40"), ("INBOX.AI-Review", "42")]
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

    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeUidValidityMismatchIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path).model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.processed == 1
    assert report.cleanup_pass_processed == 0
    assert report.cleanup_uidvalidity_mismatch == 1
    assert FakeUidValidityMismatchIMAPClient.deleted == [("INBOX.AI-Review", "42")]
    still_pending = manager.get_by_message_id(settings.default_mailbox_id(), "cleanup-msg@example.com")
    assert still_pending is not None
    assert still_pending.status.value == "cleanup_pending"
    assert still_pending.action_taken == ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING.value

    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    mismatch_entries = [line for line in audit_lines if line.get("action_taken") == "cleanup_uidvalidity_mismatch"]
    assert len(mismatch_entries) == 1


def test_process_inbox_keeps_cleanup_pending_when_cleanup_delete_fails(monkeypatch, tmp_path: Path) -> None:
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

    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeFailingCleanupIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path).model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.processed == 0
    assert report.cleanup_pass_processed == 0
    assert report.cleanup_pass_failed == 1
    assert report.cleanup_uidvalidity_mismatch == 0
    assert FakeFailingCleanupIMAPClient.deleted == [("INBOX.AI-Review", "40"), ("INBOX.AI-Review", "42")]

    still_pending = manager.get_by_message_id(settings.default_mailbox_id(), "cleanup-msg@example.com")
    assert still_pending is not None
    assert still_pending.status.value == "cleanup_pending"
    assert still_pending.action_taken == ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING.value

    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    cleanup_entries = [line for line in audit_lines if line.get("action_taken") == ActionTaken.MOVE_COPY_SUCCEEDED_CLEANUP_PENDING.value]
    assert len(cleanup_entries) == 2


def test_process_inbox_end_to_end_persists_rule_and_llm_outputs(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

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

    manager = StateManager(tmp_path / "state.sqlite")
    complaint = manager.get_by_message_id(settings.default_mailbox_id(), "<complaint-1@example.com>")
    question = manager.get_by_message_id(settings.default_mailbox_id(), "<question-1@example.com>")
    assert complaint is not None
    assert question is not None
    assert complaint.category == "complaint"
    assert complaint.sender == "[redacted]"
    assert complaint.subject == "[redacted]"
    assert complaint.sender_sha256 is not None
    assert complaint.subject_sha256 is not None
    assert complaint.action_taken == "move_skip_ai"
    assert complaint.target_folder == "INBOX.Complaints"
    assert complaint.target_uid == "142"
    assert complaint.uidvalidity == "999"
    assert complaint.rule_hit == "complaint pattern matched"
    assert complaint.model_name is None
    assert question.category == "question"
    assert question.action_taken == "move_route_from_llm"
    assert question.target_folder == "INBOX.Questions"
    assert question.target_uid == "142"
    assert question.uidvalidity == "999"
    assert question.model_name == settings.ollama_model
    assert question.model_latency_ms == 12
    assert question.draft_path is not None

    draft_path = Path(question.draft_path)
    assert draft_path.exists()
    draft_payload = json.loads(draft_path.read_text(encoding="utf-8"))
    assert draft_payload["category"] == "question"
    assert draft_payload["sender"] == "[redacted]"
    assert "cena" in draft_payload["draft_reply"].lower()

    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(audit_lines) == 2
    actions = {line["action_taken"] for line in audit_lines}
    assert actions == {"move_skip_ai", "move_route_from_llm"}
    llm_entry = next(line for line in audit_lines if line["action_taken"] == "move_route_from_llm")
    complaint_entry = next(line for line in audit_lines if line["action_taken"] == "move_skip_ai")
    assert complaint_entry["category"] == "complaint"
    assert "message_id_sha256" in llm_entry
    assert "draft_path_sha256" in llm_entry


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


def test_process_inbox_routes_to_uncertain_when_llm_fails(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    FakeIMAPClient.copied = []
    FakeIMAPClient.flagged = []
    FakeIMAPClient.deleted = []
    FakeIMAPClient.validated = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeFailingLLMGateway)
    settings = make_settings(tmp_path).model_copy(update={"dry_run": False})

    report = process_inbox(settings)

    assert report.processed == 0
    assert report.uncertain == 1
    assert report.failed == 0
    assert FakeIMAPClient.copied == [("INBOX.AI-Review", "42", "INBOX.AI-Uncertain")]
    assert FakeIMAPClient.deleted == [("INBOX.AI-Review", "42")]

    manager = StateManager(tmp_path / "state.sqlite")
    record = manager.get_by_message_id(settings.default_mailbox_id(), "<test-1@example.com>")
    assert record is not None
    assert record.status.value == "uncertain"
    assert record.action_taken == "move_route_uncertain_llm_failure"
    assert record.target_folder == "INBOX.AI-Uncertain"
    assert record.target_uid == "142"
    assert record.error_message is not None

    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit_lines[0]["action_taken"] == "move_route_uncertain_llm_failure"


def test_process_mailboxes_isolates_mailbox_preflight_failures(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    manifest = tmp_path / "mailboxes.json"
    manifest.write_text(
        json.dumps(
            {
                "mailboxes": [
                    {"mailbox_id": "broken", "imap_user": "broken@example.com", "imap_pass": "secret-a"},
                    {"mailbox_id": "healthy", "imap_user": "healthy@example.com", "imap_pass": "secret-b"},
                ]
            }
        ),
        encoding="utf-8",
    )

    class MixedIMAPClient(FakeMultiMailboxIMAPClient):
        def validate_runtime_setup(self, *, source_folder: str, target_folders: list[str], dry_run: bool) -> None:
            if self.mailbox.mailbox_id == "broken":
                raise RuntimeError("missing target folder")

    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", MixedIMAPClient)
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
    broken = next(item for item in report.mailbox_reports if item.mailbox_id == "broken")
    healthy = next(item for item in report.mailbox_reports if item.mailbox_id == "healthy")
    assert broken.failed == 1
    assert healthy.simulated == 1


def test_process_inbox_dry_run_audit_log_redacts_pii(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path)  # DRY_RUN=True, AUDIT_REDACT_PII defaults to True

    process_inbox(settings)

    audit_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    payload = json.loads(audit_lines[0])

    # PII fields must be absent
    for field in ("sender", "subject", "message_id"):
        assert field not in payload, f"PII field '{field}' must not appear in audit log"

    # sha256 hashes must be present and non-empty
    for field in ("sender", "subject", "message_id"):
        sha_key = f"{field}_sha256"
        assert sha_key in payload, f"Expected '{sha_key}' in audit log"
        assert isinstance(payload[sha_key], str) and len(payload[sha_key]) == 64, \
            f"Expected 64-char sha256 hex for '{sha_key}', got {payload.get(sha_key)!r}"


class FakeAuthFailingIMAPClient:
    def __init__(self, mailbox) -> None:
        pass

    def __enter__(self) -> "FakeAuthFailingIMAPClient":
        from mail_ai_agent.imap_client import IMAPAuthError
        raise IMAPAuthError("IMAP authentication failed for user@example.com: AUTHENTICATIONFAILED")

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class FakeTwoCandidateIMAPClient(FakeIMAPClient):
    def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
        first = EmailMessage()
        first["From"] = "bad@example.com"
        first["Subject"] = "Uszkodzona wiadomosc"
        first["Message-ID"] = "<bad-1@example.com>"
        first.set_content("to bedzie blad parsera")

        second = EmailMessage()
        second["From"] = "client@example.com"
        second["Subject"] = "Pytanie o cenę"
        second["Message-ID"] = "<ok-1@example.com>"
        second.set_content("Jaka jest cena usługi manicure?")
        return [
            CandidateMessage(uid="41", uidvalidity="999", internaldate=None, raw_bytes=first.as_bytes()),
            CandidateMessage(uid="42", uidvalidity="999", internaldate=None, raw_bytes=second.as_bytes()),
        ]


class FakeBrokenParseIMAPClient(FakeIMAPClient):
    _msg_deleted: ClassVar[bool] = False

    def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
        if FakeBrokenParseIMAPClient._msg_deleted:
            return []
        message = EmailMessage()
        message["From"] = "bad@example.com"
        message["Subject"] = "Uszkodzona wiadomosc"
        message["Message-ID"] = "<bad-1@example.com>"
        message.set_content("to bedzie blad parsera")
        return [CandidateMessage(uid="41", uidvalidity="999", internaldate=None, raw_bytes=message.as_bytes())]

    def delete_message(self, folder: str, uid: str) -> None:
        super().delete_message(folder, uid)
        FakeBrokenParseIMAPClient._msg_deleted = True


class FakeConflictDuplicateIMAPClient(FakeIMAPClient):
    def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
        message = EmailMessage()
        message["From"] = "spam@example.com"
        message["Subject"] = "Promocja tygodnia"
        message["Message-ID"] = "<conflict@example.com>"
        message.set_content("Kliknij tutaj po oferte tygodnia")
        return [CandidateMessage(uid="99", uidvalidity="999", internaldate=None, raw_bytes=message.as_bytes())]


def test_process_inbox_auth_failure_sets_flag_and_logs_critical(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeAuthFailingIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path)

    report = process_inbox(settings)

    assert report.imap_auth_failures == 1
    assert report.mailbox_reports[0].imap_auth_failed is True

    audit_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    auth_lines = [line for line in audit_lines if '"imap_auth_failed"' in line]
    assert len(auth_lines) == 1
    payload = json.loads(auth_lines[0])
    assert payload["action_taken"] == "imap_auth_failed"
    assert payload["level"] == "CRITICAL"


def test_process_inbox_continues_after_parse_failure(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox
    from mail_ai_agent.schemas import ParsedEmail

    def fake_parse_email(raw_bytes: bytes, settings: Settings) -> ParsedEmail:
        text = raw_bytes.decode("utf-8", errors="ignore")
        if "<bad-1@example.com>" in text:
            raise ValueError("broken MIME")
        return ParsedEmail(
            message_id="<ok-1@example.com>",
            sender="client@example.com",
            subject="Pytanie o cenę",
            normalized_body="Jaka jest cena usługi manicure?",
            plain_text_body="Jaka jest cena usługi manicure?",
        )

    FakeTwoCandidateIMAPClient.copied = []
    FakeTwoCandidateIMAPClient.flagged = []
    FakeTwoCandidateIMAPClient.deleted = []
    FakeTwoCandidateIMAPClient.validated = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeTwoCandidateIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    monkeypatch.setattr("mail_ai_agent.message_processor.parse_email", fake_parse_email)
    settings = make_settings(tmp_path)

    report = process_inbox(settings)

    assert report.candidates_seen == 2
    assert report.failed == 1
    assert report.simulated == 1
    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    actions = [line["action_taken"] for line in audit_lines]
    assert actions == ["failed_parse", "simulate_route_from_llm"]


def test_process_inbox_routes_parse_failure_to_uncertain_once_in_prod(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    def fake_parse_email(raw_bytes: bytes, settings: Settings) -> ParsedEmail:
        raise ValueError("broken MIME")

    FakeBrokenParseIMAPClient.copied = []
    FakeBrokenParseIMAPClient.deleted = []
    FakeBrokenParseIMAPClient.validated = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeBrokenParseIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    monkeypatch.setattr("mail_ai_agent.message_processor.parse_email", fake_parse_email)
    settings = make_settings(tmp_path).model_copy(update={"dry_run": False})

    first = process_inbox(settings)
    first_copied = list(FakeBrokenParseIMAPClient.copied)
    first_deleted = list(FakeBrokenParseIMAPClient.deleted)
    second = process_inbox(settings)

    assert first.uncertain == 1
    assert first.failed == 0
    assert second.uncertain == 0
    assert second.candidates_seen == 0  # Message deleted from source, no candidates to process
    assert first_copied == [("INBOX.AI-Review", "41", "INBOX.AI-Uncertain")]
    assert first_deleted == [("INBOX.AI-Review", "41")]

    message = EmailMessage()
    message["From"] = "bad@example.com"
    message["Subject"] = "Uszkodzona wiadomosc"
    message["Message-ID"] = "<bad-1@example.com>"
    message.set_content("to bedzie blad parsera")
    fingerprint = hashlib.sha256(message.as_bytes()).hexdigest()

    manager = StateManager(tmp_path / "state.sqlite")
    records = manager.get_by_fingerprint(settings.default_mailbox_id(), fingerprint)
    assert records is not None
    assert records.status.value == "uncertain"
    assert records.target_folder == "INBOX.AI-Uncertain"

    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit_lines[0]["action_taken"] == "move_route_uncertain_parse_failure"
    # Second run has no candidates (message was deleted), so only one audit line


def test_process_inbox_cleans_source_message_when_record_already_processed(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    FakeIMAPClient.copied = []
    FakeIMAPClient.flagged = []
    FakeIMAPClient.deleted = []
    FakeIMAPClient.validated = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)

    settings = make_settings(tmp_path).model_copy(update={"dry_run": False})
    manager = StateManager(tmp_path / "state.sqlite")

    message = EmailMessage()
    message["From"] = "client@example.com"
    message["Subject"] = "Pytanie o cenę"
    message["Message-ID"] = "<test-1@example.com>"
    message.set_content("Jaka jest cena usługi manicure?")
    parsed = ParsedEmail.model_validate(
        {
            "message_id": "<test-1@example.com>",
            "sender": "client@example.com",
            "subject": "Pytanie o cenę",
            "plain_text_body": "Jaka jest cena usługi manicure?",
            "normalized_body": "Jaka jest cena usługi manicure?",
        }
    )

    lease = manager.acquire_lease(
        mailbox_id=settings.default_mailbox_id(),
        message_id=parsed.message_id,
        fingerprint=compute_message_fingerprint(parsed),
        content_fingerprint=compute_content_fingerprint(parsed),
        imap_uid="42",
        uidvalidity="999",
        sender=parsed.sender,
        sender_sha256=None,
        subject=parsed.subject,
        subject_sha256=None,
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id=settings.worker_id,
        lease_seconds=settings.processing_lease_seconds,
        max_retries=settings.max_retries,
    )
    assert lease.record is not None
    manager.mark_processed(
        lease.record.id,
        category="question",
        confidence=0.91,
        target_folder="INBOX.Questions",
        target_uid="142",
        action_taken="move_route_from_llm",
    )

    report = process_inbox(settings)

    assert report.processed == 0
    assert report.skipped == 1
    assert FakeIMAPClient.deleted == [("INBOX.AI-Review", "42")]

    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit_lines[-1]["action_taken"] == "cleanup_source_already_done"


def test_process_inbox_cleans_source_message_for_safe_processed_conflict(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    FakeConflictDuplicateIMAPClient.copied = []
    FakeConflictDuplicateIMAPClient.flagged = []
    FakeConflictDuplicateIMAPClient.deleted = []
    FakeConflictDuplicateIMAPClient.validated = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeConflictDuplicateIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)

    settings = make_settings(tmp_path).model_copy(update={"dry_run": False})
    manager = StateManager(tmp_path / "state.sqlite")
    parsed = ParsedEmail.model_validate(
        {
            "message_id": "<conflict@example.com>",
            "sender": "spam@example.com",
            "subject": "Promocja tygodnia",
            "plain_text_body": "Kliknij tutaj po oferte tygodnia",
            "normalized_body": "Kliknij tutaj po oferte tygodnia",
        }
    )
    fingerprint = compute_message_fingerprint(parsed)
    content_fingerprint = compute_content_fingerprint(parsed)

    by_message_id = manager.acquire_lease(
        mailbox_id=settings.default_mailbox_id(),
        message_id=parsed.message_id,
        fingerprint="older-message-fingerprint",
        content_fingerprint="older-message-content-fingerprint",
        imap_uid="10",
        uidvalidity="999",
        sender=parsed.sender,
        sender_sha256=None,
        subject=parsed.subject,
        subject_sha256=None,
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=settings.processing_lease_seconds,
        max_retries=settings.max_retries,
    )
    assert by_message_id.record is not None
    manager.mark_processed(
        by_message_id.record.id,
        category="other",
        confidence=0.7,
        target_folder="INBOX.Other",
        target_uid="110",
        action_taken="move_route_from_llm",
    )

    by_fingerprint = manager.acquire_lease(
        mailbox_id=settings.default_mailbox_id(),
        message_id="<older-copy@example.com>",
        fingerprint=fingerprint,
        content_fingerprint=content_fingerprint,
        imap_uid="11",
        uidvalidity="999",
        sender=parsed.sender,
        sender_sha256=None,
        subject=parsed.subject,
        subject_sha256=None,
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-2",
        lease_seconds=settings.processing_lease_seconds,
        max_retries=settings.max_retries,
    )
    assert by_fingerprint.record is not None
    manager.mark_processed(
        by_fingerprint.record.id,
        category="other",
        confidence=0.71,
        target_folder="INBOX.Other",
        target_uid="111",
        action_taken="move_route_from_llm",
    )

    report = process_inbox(settings)

    assert report.processed == 0
    assert report.conflicts == 1
    assert report.skipped == 0
    assert FakeConflictDuplicateIMAPClient.deleted == [("INBOX.AI-Review", "99")]

    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit_lines[-1]["action_taken"] == "cleanup_source_conflict_duplicate"


def test_process_inbox_cleans_source_message_for_processed_message_id_mismatch(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    FakeConflictDuplicateIMAPClient.copied = []
    FakeConflictDuplicateIMAPClient.flagged = []
    FakeConflictDuplicateIMAPClient.deleted = []
    FakeConflictDuplicateIMAPClient.validated = []
    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeConflictDuplicateIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)

    settings = make_settings(tmp_path).model_copy(update={"dry_run": False})
    manager = StateManager(tmp_path / "state.sqlite")
    parsed = ParsedEmail.model_validate(
        {
            "message_id": "<conflict@example.com>",
            "sender": "spam@example.com",
            "subject": "Promocja tygodnia",
            "plain_text_body": "Kliknij tutaj po oferte tygodnia",
            "normalized_body": "Kliknij tutaj po oferte tygodnia",
        }
    )

    existing = manager.acquire_lease(
        mailbox_id=settings.default_mailbox_id(),
        message_id=parsed.message_id,
        fingerprint="older-message-fingerprint",
        content_fingerprint="older-message-content-fingerprint",
        imap_uid="10",
        uidvalidity="999",
        sender=parsed.sender,
        sender_sha256=None,
        subject=parsed.subject,
        subject_sha256=None,
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=settings.processing_lease_seconds,
        max_retries=settings.max_retries,
    )
    assert existing.record is not None
    manager.mark_processed(
        existing.record.id,
        category="other",
        confidence=0.7,
        target_folder="INBOX.Other",
        target_uid="110",
        action_taken="move_route_from_llm",
    )

    report = process_inbox(settings)

    assert report.processed == 0
    assert report.conflicts == 1
    assert report.skipped == 0
    assert FakeConflictDuplicateIMAPClient.deleted == [("INBOX.AI-Review", "99")]

    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit_lines[-1]["action_taken"] == "cleanup_source_conflict_duplicate"
