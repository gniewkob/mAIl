import pytest
import sys
from unittest.mock import MagicMock

# Mock dependencies that might be missing in the environment
# and are imported by email_parser or its dependencies.
missing_modules = ["pydantic", "pydantic_settings", "bs4", "prometheus_client", "requests"]
for module_name in missing_modules:
    if module_name not in sys.modules:
        mock_module = MagicMock()
        if module_name == "pydantic":
            def mock_init(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
            mock_module.BaseModel = type("BaseModel", (), {"__init__": mock_init})
        if module_name == "pydantic_settings":
            def mock_init(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
            mock_module.BaseSettings = type("BaseSettings", (), {"__init__": mock_init})
        sys.modules[module_name] = mock_module

from mail_ai_agent.email_parser import normalize_body

@pytest.mark.parametrize("body, max_chars, expected", [
    # Basic text
    ("Hello world", 100, "Hello world"),
    # Truncation
    ("Hello world", 5, "Hello"),
    # Line endings
    ("Line 1\r\nLine 2", 100, "Line 1\nLine 2"),
    # Excessive newlines (collapsed by logic and regex)
    ("Line 1\n\n\n\nLine 2", 100, "Line 1\n\nLine 2"),
    # Quoted text - On ... wrote:
    ("Hello\n\nOn Mon, Jan 1, 2023 at 10:00 AM User <user@example.com> wrote:\n> Quote", 100, "Hello"),
    # Quoted text - W dniu ... napisano:
    ("Cześć\n\nW dniu 1 stycznia 2023 10:00 User <user@example.com> napisano:\n> Cytat", 100, "Cześć"),
    # Quoted text - >
    ("Hello\n\n> Quoted line", 100, "Hello"),
    # Quoted text - From:
    ("Hello\n\nFrom: user@example.com\nSent: Monday...", 100, "Hello"),
    # Quoted text - Od:
    ("Cześć\n\nOd: user@example.com\nWysłano: poniedziałek...", 100, "Cześć"),
    # Signature - --
    ("Hello\n-- \nSignature", 100, "Hello"),
    # Signature - Sent from my iPhone
    ("Hello\nSent from my iPhone", 100, "Hello"),
    # Signature - Wysłane z iPhone'a
    ("Cześć\nWysłane z iPhone'a", 100, "Cześć"),
    # Signature - Pozdrawiam
    ("Cześć\nPozdrawiam,", 100, "Cześć"),
    # Signature - Best regards
    ("Hello\nBest regards.", 100, "Hello"),
    # Disclaimer - Polish
    ("Hello\nTen e-mail jest poufny", 100, "Hello"),
    # Disclaimer - English
    ("Hello\nThis e-mail is confidential", 100, "Hello"),
    # Empty input
    ("", 100, ""),
    # Whitespace only
    ("   \n   ", 100, ""),
    # Mixed newlines and whitespace
    ("Line 1\n   \nLine 2", 100, "Line 1\n\nLine 2"),
])
def test_normalize_body_parametrized(body, max_chars, expected):
    assert normalize_body(body, max_chars) == expected

def test_normalize_body_edge_cases():
    # Max chars exactly at the end of content
    assert normalize_body("Hello", 5) == "Hello"
    # Max chars 1
    assert normalize_body("Hello", 1) == "H"
    # Max chars 0
    assert normalize_body("Hello", 0) == ""

def test_normalize_body_case_insensitivity_disclaimer():
    # Disclaimer check is case insensitive
    assert normalize_body("Hello\nTHIS E-MAIL IS CONFIDENTIAL", 100) == "Hello"

def test_normalize_body_strips_result():
    # Resulting string should be stripped
    assert normalize_body("  Hello  \n\n  ", 100) == "Hello"
