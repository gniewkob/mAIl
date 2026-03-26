from __future__ import annotations

import os

import pytest

from mail_ai_agent.config import Settings
from mail_ai_agent.llm_gateway import LLMGateway
from mail_ai_agent.schemas import ParsedEmail


pytestmark = pytest.mark.integration


def _integration_enabled() -> bool:
    return os.getenv("RUN_LIVE_OLLAMA_TESTS") == "1"


@pytest.mark.skipif(not _integration_enabled(), reason="Set RUN_LIVE_OLLAMA_TESTS=1 to run live Ollama integration tests")
def test_live_ollama_returns_valid_classification() -> None:
    settings = Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
    )
    gateway = LLMGateway(settings)
    parsed = ParsedEmail(
        sender="klient@example.com",
        subject="Pytanie o cenę manicure",
        normalized_body="Dzień dobry, jaka jest cena manicure hybrydowego?",
    )

    classification, latency_ms = gateway.classify(parsed)

    assert classification.category in {"question", "other"}
    assert classification.summary
    assert latency_ms >= 0
