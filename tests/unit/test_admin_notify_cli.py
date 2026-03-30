from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import mail_ai_agent.admin_notify_cli as cli_mod


def _make_settings(**kwargs):
    from mail_ai_agent.config import Settings
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="u@example.com",
        IMAP_PASS="secret",
        ADMIN_NOTIFY_EMAIL="admin@example.com",
        WORKER_ID="test-worker",
        **kwargs,
    )


def test_dry_run_prints_email(capsys):
    settings = _make_settings()

    mock_imap = MagicMock()
    mock_imap.supports_uidplus.return_value = False
    mock_imap_cm = MagicMock()
    mock_imap_cm.__enter__ = MagicMock(return_value=mock_imap)
    mock_imap_cm.__exit__ = MagicMock(return_value=None)

    importlib.reload(cli_mod)
    with patch("mail_ai_agent.admin_notify_cli.Settings", return_value=settings), \
         patch("mail_ai_agent.admin_notify_cli.IMAPClient", return_value=mock_imap_cm), \
         patch.object(sys, "argv", ["admin_notify_cli", "--dry-run"]):
        cli_mod.main()

    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out
    assert "UIDPLUS" in captured.out
    assert "imap.example.com" in captured.out


def test_uidplus_supported_skips_notification():
    settings = _make_settings()

    mock_imap = MagicMock()
    mock_imap.supports_uidplus.return_value = True
    mock_imap_cm = MagicMock()
    mock_imap_cm.__enter__ = MagicMock(return_value=mock_imap)
    mock_imap_cm.__exit__ = MagicMock(return_value=None)

    importlib.reload(cli_mod)
    with patch("mail_ai_agent.admin_notify_cli.Settings", return_value=settings), \
         patch("mail_ai_agent.admin_notify_cli.IMAPClient", return_value=mock_imap_cm), \
         patch("mail_ai_agent.admin_notify_cli.send_admin_email") as mock_send, \
         patch.object(sys, "argv", ["admin_notify_cli"]):
        cli_mod.main()

    mock_send.assert_not_called()


def test_no_uidplus_sends_email():
    settings = _make_settings(
        SMTP_HOST="smtp.example.com",
        SMTP_USER="smtp@example.com",
        SMTP_PASS="smtppass",
    )

    mock_imap = MagicMock()
    mock_imap.supports_uidplus.return_value = False
    mock_imap_cm = MagicMock()
    mock_imap_cm.__enter__ = MagicMock(return_value=mock_imap)
    mock_imap_cm.__exit__ = MagicMock(return_value=None)

    importlib.reload(cli_mod)
    with patch("mail_ai_agent.admin_notify_cli.Settings", return_value=settings), \
         patch("mail_ai_agent.admin_notify_cli.IMAPClient", return_value=mock_imap_cm), \
         patch("mail_ai_agent.admin_notify_cli.send_admin_email") as mock_send, \
         patch.object(sys, "argv", ["admin_notify_cli"]):
        cli_mod.main()

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args[1]
    assert "UIDPLUS" in call_kwargs["subject"]
