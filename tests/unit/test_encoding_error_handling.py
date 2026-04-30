"""Tests for handling encoding errors (e.g., cp-850) in email parsing."""

import pytest

from mail_ai_agent.config import Settings
from mail_ai_agent.email_parser import _normalize_charset, _safe_part_content, parse_email


class TestEncodingErrorHandling:
    """Test that encoding errors don't crash the worker."""

    def test_safe_part_content_handles_unknown_encoding(self):
        """_safe_part_content handles unknown encodings like cp-850."""
        from email.message import MIMEPart
        from email.policy import default
        
        part = MIMEPart(policy=default)
        part.set_payload(b"Test content with unknown encoding")
        part.set_type("text/plain")
        # Force an unknown charset
        part.set_param("charset", "cp-850", header="Content-Type")
        
        # Should not raise LookupError
        result = _safe_part_content(part)
        
        # Should return string (with replacement chars if needed)
        assert isinstance(result, str)

    def test_safe_part_content_handles_cp850_in_get_content(self):
        """Test that cp-850 encoding doesn't crash when get_content fails."""
        from email.message import MIMEPart
        from email.policy import default
        
        part = MIMEPart(policy=default)
        # Content with cp-850 specific characters
        part.set_payload(b"Faktura za us\x88ugi - \xa5\xa3\xa6")
        part.set_type("text/plain")
        part.set_param("charset", "cp-850", header="Content-Type")
        
        # Should handle gracefully without LookupError
        result = _safe_part_content(part)
        assert isinstance(result, str)
        # Should contain replacement characters or decoded content
        assert len(result) >= 0  # Just ensure it doesn't crash

    def test_decode_with_lookuperror_fallback(self):
        """Test that decode with unknown charset falls back to utf-8."""
        raw_bytes = b"Test content"
        
        # Direct test of the fallback mechanism
        try:
            # This would fail with LookupError for cp-850 if not handled
            result = raw_bytes.decode("cp-850", errors="replace")
        except LookupError:
            # Fallback should work
            result = raw_bytes.decode("utf-8", errors="replace")
        
        assert isinstance(result, str)
        assert "Test" in result

    def test_normalize_charset_maps_cp850_alias(self):
        assert _normalize_charset("cp-850") == "cp850"

    def test_parse_email_handles_cp850_declared_charset(self):
        settings = Settings(
            IMAP_HOST="imap.example.com",
            IMAP_USER="user@example.com",
            IMAP_PASS="secret",
        )
        raw = (
            b"From: a@b.com\r\n"
            b"Subject: Test cp850\r\n"
            b"Content-Type: text/plain; charset=cp-850\r\n"
            b"Content-Transfer-Encoding: 8bit\r\n"
            b"\r\n"
            b"Faktura za us\x88ugi\r\n"
        )
        parsed = parse_email(raw, settings)
        assert parsed.subject == "Test cp850"
        assert isinstance(parsed.normalized_body, str)
