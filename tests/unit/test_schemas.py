from __future__ import annotations

import pytest
from pydantic import ValidationError

from mail_ai_agent.schemas import LLMClassification


def test_valid_llm_classification_passes() -> None:
    payload = {
        "category": "question",
        "priority": "medium",
        "requires_reply": True,
        "confidence": 0.82,
        "summary": "Klient pyta o cenę usługi.",
        "entities": {},
        "draft_reply": None,
        "reasoning_short": "Treść jest jasnym pytaniem o ofertę.",
    }

    classification = LLMClassification.model_validate(payload)

    assert classification.category == "question"


def test_invalid_llm_classification_rejects_bad_confidence() -> None:
    payload = {
        "category": "question",
        "priority": "medium",
        "requires_reply": True,
        "confidence": 1.2,
        "summary": "Klient pyta o cenę usługi.",
        "entities": {},
        "draft_reply": None,
        "reasoning_short": "Treść jest jasnym pytaniem o ofertę.",
    }

    with pytest.raises(ValidationError):
        LLMClassification.model_validate(payload)


def test_invalid_llm_classification_rejects_extra_fields() -> None:
    payload = {
        "category": "question",
        "priority": "medium",
        "requires_reply": True,
        "confidence": 0.8,
        "summary": "Klient pyta o cenę usługi.",
        "entities": {},
        "draft_reply": None,
        "reasoning_short": "Treść jest jasnym pytaniem o ofertę.",
        "suggested_folder": "INBOX.Questions",
    }

    with pytest.raises(ValidationError):
        LLMClassification.model_validate(payload)


def test_llm_classification_accepts_billing_system_spam_newsletter_and_offer() -> None:
    from mail_ai_agent.schemas import LLMClassification
    c = LLMClassification(
        category="billing",
        priority="medium",
        requires_reply=True,
        confidence=0.8,
        summary="billing question",
        reasoning_short="looks like a billing inquiry",
    )
    assert c.category == "billing"

    c2 = LLMClassification(
        category="system",
        priority="low",
        requires_reply=False,
        confidence=0.9,
        summary="system notification",
        reasoning_short="automated system message",
    )
    assert c2.category == "system"

    c3 = LLMClassification(
        category="spam",
        priority="low",
        requires_reply=False,
        confidence=0.95,
        summary="Oczywisty spam.",
        reasoning_short="To wyglada jak wiadomosc spamowa.",
    )
    assert c3.category == "spam"

    c4 = LLMClassification(
        category="newsletter",
        priority="low",
        requires_reply=False,
        confidence=0.92,
        summary="Masowy newsletter promocyjny.",
        reasoning_short="To wyglada jak mailing subskrypcyjny.",
    )
    assert c4.category == "newsletter"

    c5 = LLMClassification(
        category="offer",
        priority="low",
        requires_reply=False,
        confidence=0.9,
        summary="Cold outreach od agencji marketingowej.",
        reasoning_short="To wyglada jak oferta handlowa B2B.",
    )
    assert c5.category == "offer"
