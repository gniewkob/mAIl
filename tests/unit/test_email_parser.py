from __future__ import annotations

from email.message import EmailMessage

from mail_ai_agent.config import Settings
from mail_ai_agent.email_parser import compute_content_fingerprint, compute_message_fingerprint, parse_email

from unittest.mock import patch
from email.parser import BytesParser
from email import policy
from mail_ai_agent.email_parser import _safe_part_content


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


def test_fingerprint_stable_across_timezones():
    from datetime import datetime, timezone, timedelta
    from mail_ai_agent.email_parser import compute_message_fingerprint
    from mail_ai_agent.schemas import ParsedEmail

    utc = datetime(2024, 3, 31, 12, 0, 0, tzinfo=timezone.utc)
    warsaw = utc.astimezone(timezone(timedelta(hours=2)))

    def make(dt):
        return ParsedEmail(message_id="<t@t>", sender="a@b.com", subject="s",
                           plain_text_body="b", normalized_body="b", date=dt)

    assert compute_message_fingerprint(make(utc)) == compute_message_fingerprint(make(warsaw))


def test_html_only_email_extracts_text() -> None:
    """HTML-only email should have non-empty plain_text_body derived from HTML."""
    message = EmailMessage()
    message["From"] = "a@b.com"
    message["Subject"] = "Test"
    message["Message-ID"] = "<t@t>"
    message.add_alternative("<html><body><p>Hello World</p></body></html>", subtype="html")
    result = parse_email(message.as_bytes(), make_settings())
    assert "Hello World" in (result.plain_text_body or result.normalized_body or "")


def test_invalid_date_header_is_none() -> None:
    """Email with an invalid Date header should have date=None instead of crashing."""
    message = EmailMessage()
    message["From"] = "a@b.com"
    message["Subject"] = "Test"
    message["Date"] = "Not a valid date string at all"
    message.set_content("body")
    result = parse_email(message.as_bytes(), make_settings())
    assert result.date is None


def test_missing_message_id_is_none() -> None:
    """Email without Message-ID header should have message_id=None."""
    message = EmailMessage()
    message["From"] = "a@b.com"
    message["Subject"] = "Test"
    message.set_content("body")
    result = parse_email(message.as_bytes(), make_settings())
    assert result.message_id is None


def test_body_truncated_at_max_body_chars() -> None:
    """normalized_body should be capped at max_body_chars characters."""
    settings = Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
        MAX_BODY_CHARS=10,
    )
    message = EmailMessage()
    message["From"] = "a@b.com"
    message["Subject"] = "Test"
    message.set_content("A" * 500)
    result = parse_email(message.as_bytes(), settings)
    assert len(result.normalized_body) <= 10


def test_encoded_word_subject_decoded() -> None:
    """RFC 2047 encoded-word subject should be decoded to plain text."""
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: =?UTF-8?B?SGVsbG8gV29ybGQ=?=\r\n"
        b"Message-ID: <t@t>\r\n"
        b"\r\n"
        b"body"
    )
    result = parse_email(raw, make_settings())
    assert result.subject == "Hello World"


def test_fingerprint_deterministic() -> None:
    """Same email bytes parsed twice should produce the same message fingerprint."""
    message = EmailMessage()
    message["From"] = "a@b.com"
    message["Subject"] = "Deterministic"
    message["Message-ID"] = "<det@test>"
    message.set_content("hello world")
    raw = message.as_bytes()
    r1 = parse_email(raw, make_settings())
    r2 = parse_email(raw, make_settings())
    assert compute_message_fingerprint(r1) == compute_message_fingerprint(r2)


def test_safe_part_content_lookup_error_unknown_charset():
    """Test when get_content raises LookupError due to unknown charset."""
    raw_msg = b"Content-Type: text/plain; charset=unknown-charset\r\n\r\nHello"
    msg = BytesParser(policy=policy.default).parsebytes(raw_msg)
    assert _safe_part_content(msg) == "Hello"


def test_safe_part_content_unicode_decode_error():
    """Test when get_content raises UnicodeDecodeError, fallback to get_payload."""
    raw_msg = b"Content-Type: text/plain; charset=utf-8\r\n\r\nHello"
    msg = BytesParser(policy=policy.default).parsebytes(raw_msg)
    with patch.object(msg, 'get_content', side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "forced")):
        assert _safe_part_content(msg) == "Hello"


def test_safe_part_content_unknown_charset_payload_fallback():
    """Test when payload needs to be decoded using unknown charset fallback."""
    raw_msg = b"Content-Type: text/plain; charset=invalid-charset\r\nContent-Transfer-Encoding: base64\r\n\r\nSGVsbG8=\n"
    msg = BytesParser(policy=policy.default).parsebytes(raw_msg)
    with patch.object(msg, 'get_content', side_effect=LookupError("unknown encoding")):
        assert _safe_part_content(msg) == "Hello"


def test_safe_part_content_non_bytes_payload():
    """Test when get_payload returns string instead of bytes."""
    raw_msg = b"Content-Type: text/plain; charset=utf-8\r\n\r\nHello"
    msg = BytesParser(policy=policy.default).parsebytes(raw_msg)
    with patch.object(msg, 'get_content', side_effect=LookupError("unknown encoding")):
        with patch.object(msg, 'get_payload', return_value="string payload"):
            assert _safe_part_content(msg) == "string payload"


def test_safe_part_content_returns_bytes_valid_charset():
    """Test when get_content returns bytes and decodes with valid charset."""
    raw_msg = b"Content-Type: text/plain; charset=utf-8\r\n\r\nHello"
    msg = BytesParser(policy=policy.default).parsebytes(raw_msg)
    with patch.object(msg, 'get_content', return_value=b"Byte content"):
        assert _safe_part_content(msg) == "Byte content"


def test_safe_part_content_returns_bytes_invalid_charset():
    """Test when get_content returns bytes and decodes with fallback charset."""
    raw_msg = b"Content-Type: text/plain; charset=invalid-charset\r\n\r\nHello"
    msg = BytesParser(policy=policy.default).parsebytes(raw_msg)
    with patch.object(msg, 'get_content', return_value=b"Byte content"):
        assert _safe_part_content(msg) == "Byte content"
