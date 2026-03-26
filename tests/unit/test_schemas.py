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

