from __future__ import annotations

import requests

from mail_ai_agent.config import Settings
from mail_ai_agent.llm_gateway import LLMGateway, _extract_json, _normalize_classification_payload
from mail_ai_agent.schemas import ParsedEmail


def make_settings() -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
        MAX_RETRIES=2,
    )


class DummyResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self) -> dict:
        return self._payload


def test_extract_json_handles_wrapped_output() -> None:
    raw = 'Here is the result {"category":"question","priority":"medium","requires_reply":true,"confidence":0.8,"summary":"x","entities":{},"draft_reply":null,"reasoning_short":"y"} thanks'

    extracted = _extract_json(raw)

    assert extracted.startswith("{")
    assert extracted.endswith("}")


def test_llm_gateway_retries_and_returns_classification(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.Timeout("timeout")
        return DummyResponse(
            {
                "response": '{"category":"question","priority":"medium","requires_reply":true,"confidence":0.8,"summary":"Klient pyta o cenę.","entities":{},"draft_reply":null,"reasoning_short":"Treść maila jest pytaniem."}'
            }
        )

    monkeypatch.setattr("mail_ai_agent.llm_gateway.requests.post", fake_post)
    gateway = LLMGateway(make_settings())
    parsed = ParsedEmail(sender="client@example.com", subject="Cena", normalized_body="Jaka jest cena?")

    classification, latency_ms = gateway.classify(parsed)

    assert calls["count"] == 2
    assert classification.category == "question"
    assert latency_ms >= 0


def test_normalize_classification_payload_accepts_empty_entities_list() -> None:
    payload = _normalize_classification_payload(
        '{"category":"question","priority":"medium","requires_reply":true,"confidence":0.8,"summary":"Klient pyta o cenę.","entities":[],"draft_reply":null,"reasoning_short":"Treść maila jest pytaniem."}'
    )

    assert payload["entities"] == {}


def test_llm_gateway_raises_after_retry_exhausted(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        return DummyResponse({"response": "not-json"}, status_code=200)

    monkeypatch.setattr("mail_ai_agent.llm_gateway.requests.post", fake_post)
    gateway = LLMGateway(make_settings())
    parsed = ParsedEmail(sender="client@example.com", subject="Cena", normalized_body="Jaka jest cena?")

    try:
        gateway.classify(parsed)
    except RuntimeError as exc:
        assert "LLM classification failed after retries" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")


def test_prompt_template_contains_email_content_delimiters() -> None:
    from mail_ai_agent.llm_gateway import PROMPT_TEMPLATE
    assert "<email_content>" in PROMPT_TEMPLATE
    assert "</email_content>" in PROMPT_TEMPLATE


def test_extract_json_handles_nested_objects() -> None:
    import json

    raw = '{"category": "question", "entities": {"name": "Jan"}, "other": "x"}'
    result = _extract_json(raw)
    parsed = json.loads(result)
    assert parsed["entities"] == {"name": "Jan"}
    assert parsed["other"] == "x"


def test_extract_json_ignores_trailing_object() -> None:
    import json

    # Model output with two JSON fragments — should return the first complete one
    raw = 'some prefix {"category": "question"} extra {"noise": true}'
    result = _extract_json(raw)
    parsed = json.loads(result)
    assert "noise" not in parsed
    assert parsed["category"] == "question"


def test_extract_json_raises_on_no_json() -> None:
    import pytest

    with pytest.raises(ValueError, match="No JSON object found"):
        _extract_json("no braces here")


def test_classify_does_not_crash_on_curly_braces_in_body(monkeypatch) -> None:
    import unittest.mock as mock
    from mail_ai_agent.llm_gateway import LLMGateway
    from mail_ai_agent.config import Settings
    from mail_ai_agent.schemas import ParsedEmail

    settings = Settings(
        IMAP_HOST="localhost",
        IMAP_USER="u",
        IMAP_PASS="p",
        MAX_RETRIES=1,
    )
    gateway = LLMGateway(settings)
    parsed = ParsedEmail(
        sender="client@example.com",
        subject="Order {order_id} update",
        normalized_body="Hello, your {item} is ready. Ref: {code}.",
    )
    good_response = '{"category": "question", "priority": "medium", "requires_reply": true, "confidence": 0.9, "summary": "order query", "entities": {}, "draft_reply": null, "reasoning_short": "customer asking about order"}'

    with mock.patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = {"response": good_response}
        mock_post.return_value.raise_for_status.return_value = None
        classification, _ = gateway.classify(parsed)
    assert classification.category == "question"


def test_classify_logs_raw_output_at_debug_on_parse_failure(monkeypatch, caplog) -> None:
    import logging
    import unittest.mock as mock
    from mail_ai_agent.llm_gateway import LLMGateway
    from mail_ai_agent.config import Settings
    from mail_ai_agent.schemas import ParsedEmail

    settings = Settings(
        IMAP_HOST="localhost",
        IMAP_USER="u",
        IMAP_PASS="p",
        MAX_RETRIES=1,
    )
    gateway = LLMGateway(settings)
    parsed = ParsedEmail(sender="a@b.com", subject="test", normalized_body="body")

    with mock.patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = {"response": "not valid json at all XYZ"}
        mock_post.return_value.raise_for_status.return_value = None
        with caplog.at_level(logging.DEBUG, logger="mail_ai_agent.llm_gateway"):
            try:
                gateway.classify(parsed)
            except RuntimeError:
                pass
    assert any("not valid json at all XYZ" in record.message for record in caplog.records)
