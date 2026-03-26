from __future__ import annotations

from email.message import EmailMessage

from mail_ai_agent.config import Settings
from mail_ai_agent.email_parser import compute_content_fingerprint, compute_message_fingerprint, parse_email


def make_settings() -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
    )


def test_parse_plain_text_email() -> None:
    message = EmailMessage()
    message["From"] = "Klient <client@example.com>"
    message["To"] = "kontakt@example.com"
    message["Subject"] = "Pytanie o termin"
    message.set_content("Dzien dobry,\nCzy jest wolny termin jutro?\n\nPozdrawiam")

    parsed = parse_email(message.as_bytes(), make_settings())

    assert parsed.subject == "Pytanie o termin"
    assert "wolny termin" in parsed.normalized_body
    assert parsed.attachment_metadata == []


def test_html_fallback_is_used_when_plain_text_missing() -> None:
    message = EmailMessage()
    message["From"] = "Klient <client@example.com>"
    message["Subject"] = "Cena"
    message.add_alternative("<html><body><p>Jaka jest cena usługi?</p></body></html>", subtype="html")

    parsed = parse_email(message.as_bytes(), make_settings())

    assert "Jaka jest cena usługi?" in parsed.normalized_body


def test_quoted_thread_and_signature_are_removed() -> None:
    message = EmailMessage()
    message["From"] = "Klient <client@example.com>"
    message["Subject"] = "Rezerwacja"
    message.set_content(
        "Prosze o termin w piatek.\n\nSent from my iPhone\n\nOn Tue someone wrote:\n> stary watek"
    )

    parsed = parse_email(message.as_bytes(), make_settings())

    assert parsed.normalized_body == "Prosze o termin w piatek."


def test_fingerprint_is_stable_for_same_message() -> None:
    message = EmailMessage()
    message["From"] = "client@example.com"
    message["Subject"] = "Pytanie"
    message.set_content("Czy salon jest otwarty w sobote?")
    settings = make_settings()

    first = parse_email(message.as_bytes(), settings)
    second = parse_email(message.as_bytes(), settings)

    assert compute_message_fingerprint(first) == compute_message_fingerprint(second)
    assert compute_content_fingerprint(first) == compute_content_fingerprint(second)


def test_fingerprint_changes_when_body_changes() -> None:
    settings = make_settings()
    first = EmailMessage()
    first["From"] = "client@example.com"
    first["Subject"] = "Pytanie"
    first.set_content("Wersja A")

    second = EmailMessage()
    second["From"] = "client@example.com"
    second["Subject"] = "Pytanie"
    second.set_content("Wersja B")

    parsed_first = parse_email(first.as_bytes(), settings)
    parsed_second = parse_email(second.as_bytes(), settings)

    assert compute_message_fingerprint(parsed_first) != compute_message_fingerprint(parsed_second)
    assert compute_content_fingerprint(parsed_first) != compute_content_fingerprint(parsed_second)


def test_message_fingerprint_changes_when_message_id_changes_for_same_content() -> None:
    settings = make_settings()
    first = EmailMessage()
    first["From"] = "client@example.com"
    first["Subject"] = "Faktura"
    first["Message-ID"] = "<a@example.com>"
    first.set_content("Nowa Faktura INTERNET")

    second = EmailMessage()
    second["From"] = "client@example.com"
    second["Subject"] = "Faktura"
    second["Message-ID"] = "<b@example.com>"
    second.set_content("Nowa Faktura INTERNET")

    parsed_first = parse_email(first.as_bytes(), settings)
    parsed_second = parse_email(second.as_bytes(), settings)

    assert compute_message_fingerprint(parsed_first) != compute_message_fingerprint(parsed_second)
    assert compute_content_fingerprint(parsed_first) == compute_content_fingerprint(parsed_second)


def test_message_fingerprint_changes_when_date_changes_and_message_id_missing() -> None:
    settings = make_settings()
    first = EmailMessage()
    first["From"] = "client@example.com"
    first["Subject"] = "Faktura"
    first["Date"] = "Thu, 26 Mar 2026 10:00:00 +0000"
    first.set_content("Nowa Faktura INTERNET")

    second = EmailMessage()
    second["From"] = "client@example.com"
    second["Subject"] = "Faktura"
    second["Date"] = "Fri, 27 Mar 2026 10:00:00 +0000"
    second.set_content("Nowa Faktura INTERNET")

    parsed_first = parse_email(first.as_bytes(), settings)
    parsed_second = parse_email(second.as_bytes(), settings)

    assert compute_message_fingerprint(parsed_first) != compute_message_fingerprint(parsed_second)
    assert compute_content_fingerprint(parsed_first) == compute_content_fingerprint(parsed_second)
