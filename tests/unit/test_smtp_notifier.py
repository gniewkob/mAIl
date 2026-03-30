from __future__ import annotations

import ssl
import smtplib
from unittest.mock import MagicMock, patch

from mail_ai_agent.config import Settings


def _smtp_settings() -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="u@example.com",
        IMAP_PASS="secret",
        SMTP_HOST="smtp.example.com",
        SMTP_PORT=587,
        SMTP_USER="smtp@example.com",
        SMTP_PASS="smtppass",
        SMTP_FROM="from@example.com",
        ADMIN_NOTIFY_EMAIL="admin@example.com",
    )


def test_starttls_uses_default_ssl_context():
    """send_admin_email must call starttls with ssl.create_default_context(), not bare starttls()."""
    from mail_ai_agent.smtp_notifier import send_admin_email

    mock_smtp = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__ = lambda s: mock_smtp
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
        send_admin_email(_smtp_settings(), subject="Test", body="body")

    starttls_calls = mock_smtp.starttls.call_args_list
    assert len(starttls_calls) == 1
    kwargs = starttls_calls[0].kwargs
    assert "context" in kwargs, "starttls() must pass context= argument"
    assert isinstance(kwargs["context"], ssl.SSLContext), "context must be ssl.SSLContext"
    assert kwargs["context"].verify_mode == ssl.CERT_REQUIRED


def test_send_admin_email_sends_message():
    """send_admin_email sends correctly addressed message."""
    from mail_ai_agent.smtp_notifier import send_admin_email

    mock_smtp = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__ = lambda s: mock_smtp
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
        send_admin_email(_smtp_settings(), subject="Hello", body="World")

    mock_smtp.send_message.assert_called_once()
    msg = mock_smtp.send_message.call_args[0][0]
    assert msg["Subject"] == "Hello"
    assert msg["To"] == "admin@example.com"


def test_send_admin_email_raises_without_smtp_host():
    """send_admin_email raises ValueError when SMTP_HOST not set."""
    from mail_ai_agent.smtp_notifier import send_admin_email

    settings = Settings(IMAP_HOST="h", IMAP_USER="u", IMAP_PASS="p")
    try:
        send_admin_email(settings, subject="x", body="y")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "SMTP_HOST" in str(exc)
