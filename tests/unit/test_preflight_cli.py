from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mail_ai_agent.preflight_cli import main


class FakePreflightIMAPClient:
    validated: list[tuple[str, tuple[str, ...], bool]] = []

    def __init__(self, mailbox) -> None:
        self.mailbox = mailbox

    def __enter__(self) -> "FakePreflightIMAPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def validate_routing_setup(self, *, source_folder: str, target_folders: list[str], dry_run: bool) -> None:
        self.validated.append((source_folder, tuple(target_folders), dry_run))
        if self.mailbox.mailbox_id == "broken":
            raise RuntimeError("missing folder")


def test_preflight_cli_reports_success(monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("mail_ai_agent.preflight_cli.IMAPClient", FakePreflightIMAPClient)
    monkeypatch.setattr(sys, "argv", ["preflight_cli"])
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "user@example.com")
    monkeypatch.setenv("IMAP_PASS", "secret")
    monkeypatch.setenv("DRY_RUN", "true")

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["results"][0]["ok"] is True


def test_preflight_cli_exits_nonzero_when_mailbox_fails(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "mailboxes.json"
    manifest.write_text(
        json.dumps(
            {
                "mailboxes": [
                    {"mailbox_id": "healthy", "imap_user": "healthy@example.com", "imap_pass": "secret-a"},
                    {"mailbox_id": "broken", "imap_user": "broken@example.com", "imap_pass": "secret-b"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("mail_ai_agent.preflight_cli.IMAPClient", FakePreflightIMAPClient)
    monkeypatch.setattr(sys, "argv", ["preflight_cli"])
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("MAILBOXES_CONFIG_PATH", str(manifest))
    monkeypatch.setenv("DRY_RUN", "false")

    with pytest.raises(SystemExit, match="1"):
        main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    broken = next(item for item in payload["results"] if item["mailbox_id"] == "broken")
    assert broken["ok"] is False
    assert "missing folder" in broken["error"]


def test_preflight_cli_exits_nonzero_on_bad_env_permissions(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.bad"
    env_file.write_text("IMAP_HOST=imap.example.com\n", encoding="utf-8")
    env_file.chmod(0o644)

    monkeypatch.setattr("mail_ai_agent.preflight_cli.IMAPClient", FakePreflightIMAPClient)
    monkeypatch.setattr(sys, "argv", ["preflight_cli", "--env-file", str(env_file)])
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "user@example.com")
    monkeypatch.setenv("IMAP_PASS", "secret")
    monkeypatch.setenv("DRY_RUN", "true")

    with pytest.raises(SystemExit, match="1"):
        main()

    output = capsys.readouterr().out
    payload = json.loads(output[output.find("{"):])
    assert payload["ok"] is False
    assert "env_file_error" in payload
