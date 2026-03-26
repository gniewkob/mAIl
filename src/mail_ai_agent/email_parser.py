from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from .config import Settings
from .schemas import AttachmentMeta, ParsedEmail

QUOTED_PATTERNS = (
    r"^On .+wrote:$",
    r"^W dniu .+ napisano:$",
    r"^>+",
    r"^From:\s",
    r"^Od:\s",
)

SIGNATURE_PATTERNS = (
    r"^--\s*$",
    r"^Sent from my iPhone$",
    r"^Wysłane z iPhone'a$",
    r"^Pozdrawiam[,.! ]*$",
    r"^Best regards[,.! ]*$",
)

DISCLAIMER_PATTERNS = (
    r"ten e-mail.*poufny",
    r"this e-mail.*confidential",
)


def parse_email(raw_bytes: bytes, settings: Settings) -> ParsedEmail:
    message = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[AttachmentMeta] = []

    if message.is_multipart():
        for part in message.walk():
            disposition = part.get_content_disposition()
            if disposition == "attachment":
                payload = part.get_payload(decode=True) or b""
                attachments.append(
                    AttachmentMeta(
                        filename=part.get_filename(),
                        mime_type=part.get_content_type(),
                        size=len(payload),
                    )
                )
                continue

            if part.get_content_maintype() == "multipart":
                continue

            content = _safe_part_content(part)
            if part.get_content_type() == "text/plain":
                plain_parts.append(content)
            elif part.get_content_type() == "text/html":
                html_parts.append(content)
    else:
        content = _safe_part_content(message)
        if message.get_content_type() == "text/html":
            html_parts.append(content)
        else:
            plain_parts.append(content)

    plain_text_body = "\n".join(filter(None, plain_parts)).strip()
    html_body = "\n".join(filter(None, html_parts)).strip() or None

    if not plain_text_body and html_body:
        plain_text_body = _html_to_text(html_body)

    normalized_body = normalize_body(plain_text_body, settings.max_body_chars)
    date_value = message.get("Date")
    parsed_date: datetime | None = None
    if date_value:
        try:
            parsed_date = parsedate_to_datetime(date_value)
        except (TypeError, ValueError, IndexError):
            parsed_date = None

    return ParsedEmail(
        message_id=message.get("Message-ID"),
        sender=message.get("From", "").strip(),
        reply_to=message.get("Reply-To"),
        to=message.get("To"),
        date=parsed_date,
        subject=(message.get("Subject") or "").strip(),
        plain_text_body=plain_text_body,
        html_body=html_body,
        normalized_body=normalized_body,
        attachment_metadata=attachments,
    )


def normalize_body(body: str, max_chars: int) -> str:
    lines = [line.rstrip() for line in body.replace("\r\n", "\n").split("\n")]
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped and cleaned and cleaned[-1] == "":
            continue
        if _matches_any(stripped, QUOTED_PATTERNS):
            break
        if _matches_any(stripped, SIGNATURE_PATTERNS):
            break
        if _matches_any(stripped.lower(), DISCLAIMER_PATTERNS):
            break
        cleaned.append(stripped)

    normalized = "\n".join(cleaned).strip()
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized[:max_chars].strip()


def compute_identity_fingerprint(parsed_email: ParsedEmail) -> str:
    source = "|".join(
        [
            _normalize_message_id(parsed_email.message_id),
            _normalize_date(parsed_email.date),
            _normalize_identity(parsed_email.sender),
            _normalize_identity(parsed_email.subject),
            parsed_email.normalized_body[:1000].strip().lower(),
        ]
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def compute_content_fingerprint(parsed_email: ParsedEmail) -> str:
    source = "|".join(
        [
            _normalize_identity(parsed_email.sender),
            _normalize_identity(parsed_email.subject),
            parsed_email.normalized_body[:1000].strip().lower(),
        ]
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def compute_fingerprint(parsed_email: ParsedEmail) -> str:
    return compute_identity_fingerprint(parsed_email)


def _safe_part_content(part) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        content = payload.decode(charset, errors="replace")
    if isinstance(content, bytes):
        return content.decode(part.get_content_charset() or "utf-8", errors="replace")
    return str(content)


def _html_to_text(content: str) -> str:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n")
    return html.unescape(text).strip()


def _normalize_identity(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _normalize_message_id(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_date(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone().isoformat()


def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)
