from __future__ import annotations

import json
from email.message import EmailMessage
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.schemas import CandidateMessage, LLMClassification, ParsedEmail


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
        return [CandidateMessage(uid="42", internaldate=None, raw_bytes=message.as_bytes())]

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> None:
        self.copied.append((source_folder, uid, target_folder))

    def set_flagged(self, folder: str, uid: str) -> None:
        self.flagged.append((folder, uid))


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
        return [CandidateMessage(uid=self.mailbox.mailbox_id, internaldate=None, raw_bytes=message.as_bytes())]

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> None:
        self.copied.append((source_folder, uid, target_folder))

    def set_flagged(self, folder: str, uid: str) -> None:
        return None


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


def test_process_inbox_dry_run_persists_state_and_audit(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent.main import process_inbox

    monkeypatch.setattr("mail_ai_agent.main.IMAPClient", FakeIMAPClient)
    monkeypatch.setattr("mail_ai_agent.main.LLMGateway", FakeLLMGateway)
    settings = make_settings(tmp_path)

    report = process_inbox(settings)

    assert FakeIMAPClient.copied == []
    assert FakeIMAPClient.flagged == []
    assert report.candidates_seen == 1
    assert report.mailboxes_processed == 1
    assert len(report.mailbox_reports) == 1
    assert report.acquired == 1
    assert report.processed == 1
    assert report.failed == 0
    audit_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    payload = json.loads(audit_lines[0])
    assert payload["mailbox_id"] == settings.default_mailbox_id()
    assert payload["status_after"] == "processed"
    assert payload["dry_run"] is True
    draft_files = list((tmp_path / "drafts").glob("*.json"))
    assert len(draft_files) == 1


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
    assert report.processed == 2
    assert [entry.mailbox_id for entry in report.mailbox_reports] == ["kontakt", "shop"]
    audit_lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {line["mailbox_id"] for line in audit_lines} == {"kontakt", "shop"}
