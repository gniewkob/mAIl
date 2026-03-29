from __future__ import annotations

import json
import sys
from pathlib import Path

from mail_ai_agent.manifest_secrets_cli import main


def test_manifest_secrets_cli_generates_env_refs(monkeypatch, tmp_path: Path, capsys) -> None:
    source = tmp_path / "mailboxes.json"
    migrated = tmp_path / "mailboxes.migrated.json"
    source.write_text(
        json.dumps(
            {
                "mailboxes": [
                    {"mailbox_id": "kontakt_salon_bw", "imap_user": "kontakt@example.com", "imap_pass": "secret-a"}
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["manifest_secrets_cli", "--input", str(source), "--output", str(migrated), "--mode", "env"],
    )

    main()

    payload = json.loads(migrated.read_text(encoding="utf-8"))
    assert payload["mailboxes"][0]["imap_pass_ref"] == "env:MAILBOX_SECRET_KONTAKT_SALON_BW"
    assert "imap_pass" not in payload["mailboxes"][0]
    assert "MAILBOX_SECRET_KONTAKT_SALON_BW" in capsys.readouterr().out


def test_manifest_secrets_cli_generates_keychain_sidecar(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "mailboxes.json"
    migrated = tmp_path / "mailboxes.migrated.json"
    sidecar = tmp_path / "keychain.sh"
    source.write_text(
        json.dumps(
            {
                "mailboxes": [
                    {"mailbox_id": "kontakt", "imap_user": "kontakt@example.com", "imap_pass": "secret-a"}
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manifest_secrets_cli",
            "--input",
            str(source),
            "--output",
            str(migrated),
            "--mode",
            "keychain",
            "--service",
            "mail-ai",
            "--sidecar-output",
            str(sidecar),
        ],
    )

    main()

    payload = json.loads(migrated.read_text(encoding="utf-8"))
    assert payload["mailboxes"][0]["imap_pass_ref"] == "keychain:mail-ai/kontakt@example.com"
    assert "imap_pass" not in payload["mailboxes"][0]
    sidecar_content = sidecar.read_text(encoding="utf-8")
    assert "security add-generic-password -U" in sidecar_content
    assert "kontakt@example.com" in sidecar_content
