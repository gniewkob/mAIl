from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.sieve_deploy_cli import deploy_all


def _settings_with_mailbox(tmp_path: Path) -> Settings:
    manifest = tmp_path / "mailboxes.json"
    manifest.write_text(
        json.dumps(
            {
                "mailboxes": [
                    {
                        "mailbox_id": "mbox_a",
                        "imap_host": "imap.example.com",
                        "imap_user": "a@example.com",
                        "imap_pass": "secret",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return Settings(MAILBOXES_CONFIG_PATH=str(manifest))


def test_deploy_all_uses_explicit_getscript_when_listscripts_missing(tmp_path: Path, monkeypatch) -> None:
    settings = _settings_with_mailbox(tmp_path)
    input_dir = tmp_path / "sieve"
    input_dir.mkdir()
    script_content = 'require ["fileinto"];\nif true { fileinto "INBOX"; }\n'
    (input_dir / "mbox_a.main.sieve").write_text(script_content, encoding="utf-8")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def authenticate_plain(self, username: str, password: str) -> None:
            return None

        def put_script(self, name: str, content: str) -> None:
            return None

        def set_active(self, name: str) -> None:
            return None

        def list_scripts(self):
            return [], []

        def get_script(self, name: str) -> str:
            return script_content

    monkeypatch.setattr("mail_ai_agent.sieve_deploy_cli.ManageSieveClient", FakeClient)
    results = deploy_all(
        settings=settings,
        input_dir=input_dir,
        script_name="main.sieve",
        port=4190,
        timeout_seconds=5,
        tls_mode="auto",
        strict_verify=True,
    )
    assert len(results) == 1
    assert results[0].verified is True
    assert results[0].verification_mode == "explicit_getscript"


def test_deploy_all_strict_verify_fails_when_getscript_mismatch(tmp_path: Path, monkeypatch) -> None:
    settings = _settings_with_mailbox(tmp_path)
    input_dir = tmp_path / "sieve"
    input_dir.mkdir()
    (input_dir / "mbox_a.main.sieve").write_text('require ["fileinto"];\n', encoding="utf-8")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def authenticate_plain(self, username: str, password: str) -> None:
            return None

        def put_script(self, name: str, content: str) -> None:
            return None

        def set_active(self, name: str) -> None:
            return None

        def list_scripts(self):
            return [], []

        def get_script(self, name: str) -> str:
            return "different"

    monkeypatch.setattr("mail_ai_agent.sieve_deploy_cli.ManageSieveClient", FakeClient)
    results = deploy_all(
        settings=settings,
        input_dir=input_dir,
        script_name="main.sieve",
        port=4190,
        timeout_seconds=5,
        tls_mode="auto",
        strict_verify=True,
    )
    assert len(results) == 1
    assert results[0].verified is False
    assert results[0].verification_mode == "failed"


def test_deploy_all_rejects_active_in_error_string_under_strict_verify(
    tmp_path: Path, monkeypatch
) -> None:
    """Server error messages echoing the script name and 'ACTIVE' are NOT proof
    of activation. Strict verification must reject such heuristic evidence."""
    settings = _settings_with_mailbox(tmp_path)
    input_dir = tmp_path / "sieve"
    input_dir.mkdir()
    (input_dir / "mbox_a.main.sieve").write_text('require ["fileinto"];\n', encoding="utf-8")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def authenticate_plain(self, username: str, password: str) -> None:
            return None

        def put_script(self, name: str, content: str) -> None:
            return None

        def set_active(self, name: str) -> None:
            return None

        def list_scripts(self):
            return [], []

        def get_script(self, name: str) -> str:
            raise RuntimeError('getscript failed: "main.sieve" ACTIVE')

    monkeypatch.setattr("mail_ai_agent.sieve_deploy_cli.ManageSieveClient", FakeClient)
    results = deploy_all(
        settings=settings,
        input_dir=input_dir,
        script_name="main.sieve",
        port=4190,
        timeout_seconds=5,
        tls_mode="auto",
        strict_verify=True,
    )
    assert len(results) == 1
    assert results[0].verified is False
    assert results[0].verification_mode == "failed"
    assert results[0].verification_evidence is not None
    assert "getscript_error" in results[0].verification_evidence


def test_deploy_all_soft_pass_when_listscripts_empty_and_strict_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    """Servers that ack SETACTIVE but expose an empty LISTSCRIPTS response should
    fall back to soft_pass when strict_verify is off."""
    settings = _settings_with_mailbox(tmp_path)
    input_dir = tmp_path / "sieve"
    input_dir.mkdir()
    (input_dir / "mbox_a.main.sieve").write_text('require ["fileinto"];\n', encoding="utf-8")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def authenticate_plain(self, username: str, password: str) -> None:
            return None

        def put_script(self, name: str, content: str) -> None:
            return None

        def set_active(self, name: str) -> None:
            return None

        def list_scripts(self):
            return [], []

        def get_script(self, name: str) -> str:
            raise RuntimeError('getscript failed: NO (NONEXISTENT)')

    monkeypatch.setattr("mail_ai_agent.sieve_deploy_cli.ManageSieveClient", FakeClient)
    results = deploy_all(
        settings=settings,
        input_dir=input_dir,
        script_name="main.sieve",
        port=4190,
        timeout_seconds=5,
        tls_mode="auto",
        strict_verify=False,
    )
    assert len(results) == 1
    assert results[0].verified is True
    assert results[0].verification_mode == "soft_pass"
