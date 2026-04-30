from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mail_ai_agent.migrate_audit_log_cli import main, run_migrate_audit_log


def test_run_migrate_audit_log_dry_run_reports_changes(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                json.dumps({"category": "spam_or_offer", "target_folder": "Junk"}),
                json.dumps({"category": "spam_or_offer", "target_folder": "INBOX.Newsletter"}),
                json.dumps({"category": "spam_or_offer", "target_folder": "INBOX.Offer"}),
                json.dumps({"category": "question", "target_folder": "INBOX.Questions"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = run_migrate_audit_log(audit_log=audit_path, apply=False)

    assert payload["status"] == "ok"
    assert payload["records_seen"] == 4
    assert payload["records_changed"] == 3
    assert payload["backup_created"] is None
    content = audit_path.read_text(encoding="utf-8")
    assert "spam_or_offer" in content


def test_run_migrate_audit_log_apply_rewrites_and_creates_backup(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                json.dumps({"category": "spam_or_offer", "target_folder": "Junk"}),
                json.dumps({"category": "spam_or_offer", "target_folder": "INBOX.Newsletter"}),
                json.dumps({"category": "spam_or_offer", "target_folder": "INBOX.Other"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = run_migrate_audit_log(audit_log=audit_path, apply=True)

    assert payload["records_changed"] == 3
    assert payload["backup_created"] == str(audit_path.with_suffix(".jsonl.bak"))
    rewritten = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["category"] for row in rewritten] == ["spam", "newsletter", "offer"]
    backup_content = audit_path.with_suffix(".jsonl.bak").read_text(encoding="utf-8")
    assert "spam_or_offer" in backup_content


def test_run_migrate_audit_log_infers_newsletter_from_subject_and_sender(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        json.dumps(
            {
                "category": "spam_or_offer",
                "target_folder": "INBOX.Other",
                "sender": "Allegro <powiadomienia@allegro.pl>",
                "subject": "Sprawdź najlepsze okazje!",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = run_migrate_audit_log(audit_log=audit_path, apply=True)

    assert payload["records_changed"] == 1
    rewritten = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rewritten[0]["category"] == "newsletter"


def test_migrate_audit_log_cli_prints_json(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(json.dumps({"category": "spam_or_offer", "target_folder": "Junk"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["migrate_audit_log_cli", "--audit-log", str(audit_path)])

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["records_changed"] == 1
    assert payload["apply"] is False
