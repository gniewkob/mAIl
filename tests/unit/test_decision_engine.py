from __future__ import annotations

from mail_ai_agent.config import Settings
from mail_ai_agent.decision_engine import decide_from_llm
from mail_ai_agent.schemas import LLMClassification, LLMEntities, WorkflowStatus


def make_settings() -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
    )


def make_classification(**overrides) -> LLMClassification:
    payload = {
        "category": "question",
        "priority": "medium",
        "requires_reply": True,
        "confidence": 0.82,
        "summary": "Klient pyta o cenę.",
        "entities": LLMEntities(),
        "draft_reply": "Dzień dobry, cena zaczyna się od 100 zł.",
        "reasoning_short": "Mail jest prostym pytaniem ofertowym.",
    }
    payload.update(overrides)
    return LLMClassification.model_validate(payload)


def test_low_confidence_goes_to_uncertain() -> None:
    settings = make_settings()
    mailbox = settings.load_mailboxes()[0]
    decision = decide_from_llm(make_classification(confidence=0.6), settings, mailbox)

    assert decision.final_status == WorkflowStatus.UNCERTAIN


def test_high_confidence_complaint_gets_flagged() -> None:
    settings = make_settings()
    mailbox = settings.load_mailboxes()[0]
    decision = decide_from_llm(
        make_classification(category="complaint", priority="high", confidence=0.9, requires_reply=True),
        settings,
        mailbox,
    )

    assert decision.final_status == WorkflowStatus.PROCESSED
    assert "\\Flagged" in decision.flags
    assert decision.draft_reply is not None
