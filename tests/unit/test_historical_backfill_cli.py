from __future__ import annotations

import csv
from email.message import EmailMessage
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.schemas import CandidateMessage, LLMClassification, LLMEntities
from mail_ai_agent.historical_backfill_cli import run_historical_backfill


def _raw_email(*, subject: str, sender: str, body: str) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "team@example.com"
    message["Subject"] = subject
    message["Message-ID"] = f"<{subject.replace(' ', '-')}@example.com>"
    message.set_content(body)
    return message.as_bytes()


class FakeHistoricalIMAPClient:
    folders = ["INBOX", "Archive/2025", "INBOX.AI-Review", "INBOX.Other", "Sent"]
    candidates_by_folder: dict[str, list[CandidateMessage]] = {}
    validated: list[tuple[str, tuple[str, ...], bool]] = []
    copied: list[tuple[str, str, str]] = []
    deleted: list[tuple[str, str]] = []

    def __init__(self, mailbox) -> None:
        self.mailbox = mailbox

    def __enter__(self) -> "FakeHistoricalIMAPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    @classmethod
    def reset(cls) -> None:
        cls.validated = []
        cls.copied = []
        cls.deleted = []

    def list_folders(self) -> list[str]:
        return list(self.folders)

    def validate_routing_setup(self, *, source_folder: str, target_folders: list[str], dry_run: bool) -> None:
        self.validated.append((source_folder, tuple(target_folders), dry_run))

    def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
        return list(self.candidates_by_folder.get(folder, []))

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> str:
        self.copied.append((source_folder, uid, target_folder))
        return f"copy-{uid}"

    def delete_message(self, folder: str, uid: str) -> None:
        self.deleted.append((folder, uid))


def _make_settings() -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
        OLLAMA_MODEL="qwen3:8b",
    )


def test_historical_backfill_dry_run_stages_into_worker_source_folder_and_exports_csv(
    monkeypatch, tmp_path: Path
) -> None:
    FakeHistoricalIMAPClient.reset()
    FakeHistoricalIMAPClient.candidates_by_folder = {
        "INBOX": [
            CandidateMessage(
                uid="10",
                uidvalidity="1",
                internaldate="31-Mar-2026 08:00:00 +0000",
                raw_bytes=_raw_email(
                    subject="Oferta SEO dla salonu",
                    sender="agency@example.com",
                    body="Chcemy zaoferowac wspolprace marketingowa.",
                ),
            )
        ],
    }

    monkeypatch.setattr("mail_ai_agent.historical_backfill_cli.IMAPClient", FakeHistoricalIMAPClient)

    export_csv = tmp_path / "backfill.csv"
    payload = run_historical_backfill(
        settings=_make_settings(),
        apply=False,
        export_csv=export_csv,
    )

    mailbox_payload = payload["mailboxes"][0]
    assert mailbox_payload["folders_selected"] == ["INBOX", "Archive/2025"]
    assert payload["planned"] == 1
    assert payload["applied"] == 0
    assert payload["failed"] == 0
    assert payload["candidates_seen"] == 1

    first = mailbox_payload["results"][0]
    assert first["decision_source"] == "sieve_stage"
    assert first["category"] is None
    assert first["target_folder"] == "INBOX.AI-Review"
    assert first["action_taken"] == "stage_for_worker"

    with export_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["source_folder"] == "INBOX"


def test_historical_backfill_apply_moves_messages_for_requested_folders(monkeypatch) -> None:
    FakeHistoricalIMAPClient.reset()
    FakeHistoricalIMAPClient.candidates_by_folder = {
        "Archive/2025": [
            CandidateMessage(
                uid="21",
                uidvalidity="1",
                internaldate="31-Mar-2026 09:00:00 +0000",
                raw_bytes=_raw_email(
                    subject="Faktura za usluge",
                    sender="billing@example.com",
                    body="W zalaczeniu faktura.",
                ),
            )
        ]
    }

    monkeypatch.setattr("mail_ai_agent.historical_backfill_cli.IMAPClient", FakeHistoricalIMAPClient)

    payload = run_historical_backfill(
        settings=_make_settings(),
        apply=True,
        requested_folders=["Archive/2025", "Missing"],
    )

    mailbox_payload = payload["mailboxes"][0]
    assert mailbox_payload["folders_selected"] == ["Archive/2025"]
    assert mailbox_payload["missing_requested_folders"] == ["Missing"]
    assert payload["planned"] == 1
    assert payload["applied"] == 1
    assert payload["failed"] == 0
    assert FakeHistoricalIMAPClient.copied == [("Archive/2025", "21", "INBOX.AI-Review")]
    assert FakeHistoricalIMAPClient.deleted == [("Archive/2025", "21")]
    assert mailbox_payload["results"][0]["status"] == "applied"


def test_historical_backfill_classify_mode_keeps_direct_routing_available(monkeypatch) -> None:
    FakeHistoricalIMAPClient.reset()
    FakeHistoricalIMAPClient.candidates_by_folder = {
        "Archive/2025": [
            CandidateMessage(
                uid="30",
                uidvalidity="1",
                internaldate="31-Mar-2026 09:15:00 +0000",
                raw_bytes=_raw_email(
                    subject="Pytanie o termin",
                    sender="client@example.com",
                    body="Czy sa wolne terminy w sobote?",
                ),
            )
        ]
    }
    classification = LLMClassification(
        category="question",
        priority="medium",
        requires_reply=True,
        confidence=0.93,
        summary="Klient pyta o wolny termin.",
        entities=LLMEntities(),
        draft_reply=None,
        reasoning_short="Mail zawiera pytanie o termin.",
    )

    monkeypatch.setattr("mail_ai_agent.historical_backfill_cli.IMAPClient", FakeHistoricalIMAPClient)
    monkeypatch.setattr(
        "mail_ai_agent.historical_backfill_cli.LLMGateway.classify",
        lambda self, parsed_email: (classification, 123),
    )

    payload = run_historical_backfill(
        settings=_make_settings(),
        apply=False,
        mode="classify",
        requested_folders=["Archive/2025"],
    )

    row = payload["mailboxes"][0]["results"][0]
    assert row["decision_source"] == "llm"
    assert row["category"] == "question"
    assert row["target_folder"] == "INBOX.Questions"
