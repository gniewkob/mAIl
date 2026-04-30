from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mail_ai_agent.config import Settings
from mail_ai_agent.provision_folders_cli import main, run_provision_folders


class FakeProvisionIMAPClient:
    listed_folders: list[str] = []
    create_calls: list[tuple[str, str]] = []

    def __init__(self, mailbox) -> None:
        self.mailbox = mailbox

    def __enter__(self) -> "FakeProvisionIMAPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    @classmethod
    def reset(cls) -> None:
        cls.listed_folders = []
        cls.create_calls = []

    def list_folders(self) -> list[str]:
        return list(self.listed_folders)

    def create_folder(self, folder: str) -> None:
        self.create_calls.append((self.mailbox.mailbox_id, folder))


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
        STATE_DB_PATH=tmp_path / "state.sqlite",
        AUDIT_LOG_PATH=tmp_path / "audit.jsonl",
        DRAFT_DIR=tmp_path / "drafts",
    )


def test_run_provision_folders_dry_run_reports_missing(monkeypatch, tmp_path: Path) -> None:
    FakeProvisionIMAPClient.reset()
    FakeProvisionIMAPClient.listed_folders = ["INBOX", "INBOX.Other"]
    monkeypatch.setattr("mail_ai_agent.provision_folders_cli.IMAPClient", FakeProvisionIMAPClient)

    payload = run_provision_folders(settings=_make_settings(tmp_path), apply=False)

    assert payload["selected"] == 1
    assert payload["created"] == 0
    assert payload["existing"] == 0
    assert payload["missing"] == 3
    assert payload["failed"] == 0
    assert payload["results"][0]["folders"] == [
        {"folder": "Junk", "status": "missing"},
        {"folder": "INBOX.Newsletter", "status": "missing"},
        {"folder": "INBOX.Offer", "status": "missing"},
    ]
    assert FakeProvisionIMAPClient.create_calls == []


def test_run_provision_folders_apply_creates_only_missing(monkeypatch, tmp_path: Path) -> None:
    FakeProvisionIMAPClient.reset()
    FakeProvisionIMAPClient.listed_folders = ["INBOX", "Junk", "INBOX.Newsletter"]
    monkeypatch.setattr("mail_ai_agent.provision_folders_cli.IMAPClient", FakeProvisionIMAPClient)

    payload = run_provision_folders(settings=_make_settings(tmp_path), apply=True)

    assert payload["created"] == 1
    assert payload["existing"] == 2
    assert payload["missing"] == 0
    assert payload["failed"] == 0
    assert FakeProvisionIMAPClient.create_calls == [
        ("user_example_com", "INBOX.Offer"),
    ]


def test_provision_folders_cli_prints_json(monkeypatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    FakeProvisionIMAPClient.reset()
    FakeProvisionIMAPClient.listed_folders = ["INBOX", "Junk", "INBOX.Newsletter", "INBOX.Offer"]
    monkeypatch.setattr("mail_ai_agent.provision_folders_cli.IMAPClient", FakeProvisionIMAPClient)
    monkeypatch.setattr(sys, "argv", ["provision_folders_cli"])
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "user@example.com")
    monkeypatch.setenv("IMAP_PASS", "secret")
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.sqlite"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("DRAFT_DIR", str(tmp_path / "drafts"))

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["failed"] == 0
    assert payload["existing"] == 3
