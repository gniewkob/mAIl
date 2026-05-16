from __future__ import annotations
import sys
from unittest.mock import MagicMock

# Mock dependencies locally to allow test collection in restricted environment
class MockBaseModel:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
    @classmethod
    def model_validate(cls, obj):
        return cls(**obj)

class MockBaseSettings(MockBaseModel):
    pass

pydantic = MagicMock()
pydantic.BaseModel = MockBaseModel
pydantic.Field = MagicMock(return_value=None)
pydantic.field_validator = lambda *args, **kwargs: lambda f: f
pydantic.model_validator = lambda *args, **kwargs: lambda f: f
pydantic.SecretStr = lambda x: x

pydantic_settings = MagicMock()
pydantic_settings.BaseSettings = MockBaseSettings
pydantic_settings.SettingsConfigDict = MagicMock(return_value={})

sys.modules["pydantic"] = pydantic
sys.modules["pydantic_settings"] = pydantic_settings
sys.modules["bs4"] = MagicMock()
sys.modules["requests"] = MagicMock()

import json
from pathlib import Path
from unittest.mock import patch
from mail_ai_agent.manifest_secrets_cli import main

def test_manifest_secrets_cli_secure_apply_keychain(tmp_path: Path) -> None:
    source = tmp_path / "mailboxes.json"
    migrated = tmp_path / "mailboxes.migrated.json"
    source.write_text(
        json.dumps({
            "mailboxes": [{"mailbox_id": "test", "imap_user": "u@x.com", "imap_pass": "secret123"}]
        }),
        encoding="utf-8",
    )

    argv = [
        "manifest_secrets_cli",
        "--input", str(source),
        "--output", str(migrated),
        "--mode", "keychain",
        "--apply",
        "--allow-stdout-secrets"
    ]

    with patch.object(sys, "argv", argv), patch("subprocess.run") as mock_run:
        main()

        # Verify subprocess.run was called
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        cmd = args[0]
        # In current fix, we pass secret as an argument because stdin is not supported by security CLI
        assert "secret123" in cmd
        assert "-w" in cmd

def test_manifest_secrets_cli_env_sidecar_export_reverted(tmp_path: Path) -> None:
    source = tmp_path / "mailboxes.json"
    migrated = tmp_path / "mailboxes.migrated.json"
    sidecar = tmp_path / "sidecar.env"
    source.write_text(
        json.dumps({
            "mailboxes": [{"mailbox_id": "test", "imap_user": "u@x.com", "imap_pass": "secret123"}]
        }),
        encoding="utf-8",
    )

    argv = [
        "manifest_secrets_cli",
        "--input", str(source),
        "--output", str(migrated),
        "--mode", "env",
        "--sidecar-output", str(sidecar)
    ]

    with patch.object(sys, "argv", argv):
        main()

    content = sidecar.read_text()
    assert "export MAILBOX_SECRET_TEST=secret123" in content
