from __future__ import annotations

import ssl
import smtplib
from email.message import EmailMessage

from .config import Settings


def send_admin_email(settings: Settings, *, subject: str, body: str) -> None:
    if not settings.smtp_host:
        raise ValueError("SMTP_HOST is not configured.")
    if not settings.smtp_user:
        raise ValueError("SMTP_USER is not configured.")
    if not settings.smtp_pass:
        raise ValueError("SMTP_PASS is not configured.")
    if not settings.admin_notify_email:
        raise ValueError("ADMIN_NOTIFY_EMAIL is not configured.")

    from_addr = settings.smtp_from or settings.smtp_user
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = settings.admin_notify_email
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(settings.smtp_user, settings.smtp_pass.get_secret_value())
        smtp.send_message(msg)
