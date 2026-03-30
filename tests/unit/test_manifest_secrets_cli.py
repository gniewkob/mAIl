from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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


def test_manifest_secrets_cli_env_mode(tmp_path: Path) -> None:
    input_m = tmp_path / "m.json"
    output_m = tmp_path / "m.out.json"
    input_m.write_text(
        json.dumps({"mailboxes": [{"imap_user": "u@e.com", "imap_pass": "s", "imap_host": "h"}]}),
        encoding="utf-8",
    )
    with patch.object(sys, "argv", ["x", "--input", str(input_m), "--output", str(output_m), "--mode", "env"]):
        main()

    result = json.loads(output_m.read_text(encoding="utf-8"))
    mailbox = result["mailboxes"][0]
    assert "imap_pass" not in mailbox
    assert mailbox["imap_pass_ref"].startswith("env:")


def test_write_manifest_applies_chmod_600(tmp_path) -> None:
    import os, stat
    from mail_ai_agent.manifest_secrets_cli import _write_manifest
    out = tmp_path / "manifest.json"
    _write_manifest(out, {"mailboxes": []})
    mode = stat.S_IMODE(os.stat(out).st_mode)
    assert mode == 0o600


def test_sidecar_file_has_restricted_permissions(tmp_path: Path) -> None:
    input_m = tmp_path / "m.json"
    output_m = tmp_path / "m.out.json"
    sidecar = tmp_path / "s.sh"
    input_m.write_text(
        json.dumps({"mailboxes": [{"imap_user": "u@e.com", "imap_pass": "s", "imap_host": "h"}]}),
        encoding="utf-8",
    )
    with patch.object(
        sys,
        "argv",
        ["x", "--input", str(input_m), "--output", str(output_m), "--mode", "env", "--sidecar-output", str(sidecar)],
    ):
        main()
    assert stat.S_IMODE(os.stat(sidecar).st_mode) == 0o600
