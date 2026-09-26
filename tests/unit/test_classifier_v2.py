from __future__ import annotations

import inspect
import json

import pytest
from pydantic import SecretStr

from mail_ai_agent import rule_engine
from mail_ai_agent.classifier_v2 import HARD_RULE_REASONS, classify_v2
from mail_ai_agent.config import MailboxConfig, Settings
from mail_ai_agent.schemas import LLMActionClassification, ParsedEmail
from mail_ai_agent.sent_index import SentIndex, _h
from mail_ai_agent.shadow_v2 import ShadowV2


def _mailbox() -> MailboxConfig:
    return MailboxConfig(mailbox_id="salon", imap_host="h", imap_user="kontakt@salon-bw.pl",
                         imap_pass=SecretStr("x"), automated_sender_keep="booksy")


def _settings(tmp_path=None, **kw) -> Settings:
    extra = {"CLASSIFIER_V2_SHADOW_LOG": str(tmp_path / "shadow.jsonl"),
             "SENT_INDEX_PATH": str(tmp_path / "idx.json")} if tmp_path else {}
    return Settings(IMAP_HOST="h", IMAP_USER="u@example.com", IMAP_PASS="s", **extra, **kw)


class FakeGateway:
    def __init__(self, action="informacja", category="system", confidence=0.9, error=None):
        self.result = LLMActionClassification(action=action, category=category, confidence=confidence,
                                              reasoning_short="r")
        self.error = error
        self.calls: list[dict] = []

    def classify(self, parsed, **extra):
        self.calls.append(extra)
        if self.error:
            raise self.error
        return self.result, 5


def _index(sent_ids=(), correspondents=()) -> SentIndex:
    return SentIndex({_h(i) for i in sent_ids}, {_h(a) for a in correspondents}, {_h("kontakt@salon-bw.pl")})


def _mail(sender="Firma <biuro@firma.pl>", subject="Temat", headers=None, body="treść") -> ParsedEmail:
    return ParsedEmail(sender=sender, subject=subject, normalized_body=body, headers=headers or {})


def test_reply_to_owner_message_is_protected_even_with_spam_header() -> None:
    gw = FakeGateway(action="spam", category="spam")
    mail = _mail(headers={"in-reply-to": "<abc@salon-bw.pl>", "x-spam-flag": "YES"})
    d = classify_v2(mail, _mailbox(), _settings(), gw, _index(sent_ids=["<abc@salon-bw.pl>"]))
    assert (d.category, d.source) == ("question", "protect")
    assert gw.calls == []


def test_references_chain_also_counts() -> None:
    mail = _mail(headers={"references": "<x@a> <abc@salon-bw.pl> <y@b>"})
    d = classify_v2(mail, _mailbox(), _settings(), FakeGateway(), _index(sent_ids=["<ABC@salon-bw.pl>"]))
    assert d.source == "protect"


def test_booksy_booking_is_appointment_without_llm() -> None:
    gw = FakeGateway(action="szum", category="newsletter")
    mail = _mail(sender="Anna <no-reply@booksy.com>", subject="Anna: nowa rezerwacja piątek, 2 października")
    d = classify_v2(mail, _mailbox(), _settings(), gw, _index())
    assert d.category == "appointment" and gw.calls == []


def test_own_site_booking_counts_but_foreign_domain_does_not() -> None:
    own = _mail(sender="noreply@salon-bw.pl", subject="Nowa rezerwacja online — 2026-07-07 11:00")
    assert classify_v2(own, _mailbox(), _settings(), FakeGateway(), _index()).category == "appointment"
    foreign = _mail(sender="x@phish.example", subject="Nowa rezerwacja online")
    assert classify_v2(foreign, _mailbox(), _settings(), FakeGateway(), _index()).source != "protect"


def test_hard_rule_spam_header_skips_llm() -> None:
    gw = FakeGateway()
    d = classify_v2(_mail(headers={"x-spam-flag": "YES"}), _mailbox(), _settings(), gw, _index())
    assert (d.category, d.source) == ("spam", "rule") and gw.calls == []


def test_keyword_rule_needs_bulk_otherwise_llm_decides() -> None:
    body = "kod rabatowy na nową kolekcję"
    gw = FakeGateway(action="do_zrobienia", category="newsletter")
    d = classify_v2(_mail(body=body), _mailbox(), _settings(), gw, _index())
    assert (d.category, d.source) == ("question", "llm") and len(gw.calls) == 1
    bulk = _mail(body=body, headers={"list-unsubscribe": "<mailto:u@x>"})
    d2 = classify_v2(bulk, _mailbox(), _settings(), FakeGateway(), _index())
    assert (d2.category, d2.source) == ("newsletter", "bulk_rule")


def test_action_maps_to_category() -> None:
    s, m, ix = _settings(), _mailbox(), _index()
    assert classify_v2(_mail(), m, s, FakeGateway("do_zrobienia", "billing"), ix).category == "question"
    assert classify_v2(_mail(), m, s, FakeGateway("do_zrobienia", "appointment"), ix).category == "appointment"
    assert classify_v2(_mail(), m, s, FakeGateway("informacja", "billing"), ix).category == "billing"
    assert classify_v2(_mail(), m, s, FakeGateway("spam", "offer"), ix).category == "spam"


def test_correspondent_is_never_noise_but_billing_stays() -> None:
    ix = _index(correspondents=["biuro@firma.pl"])
    d = classify_v2(_mail(), _mailbox(), _settings(), FakeGateway("szum", "newsletter"), ix)
    assert (d.category, d.source) == ("question", "floor_correspondent")
    d2 = classify_v2(_mail(), _mailbox(), _settings(), FakeGateway("informacja", "billing"), ix)
    assert d2.category == "billing"


def test_low_confidence_noise_stays_in_inbox() -> None:
    d = classify_v2(_mail(), _mailbox(), _settings(), FakeGateway("szum", "newsletter", 0.6), _index())
    assert (d.category, d.source) == ("other", "floor_confidence")


def test_llm_error_keeps_mail_in_inbox() -> None:
    d = classify_v2(_mail(), _mailbox(), _settings(), FakeGateway(error=RuntimeError("down")), _index())
    assert (d.category, d.source) == ("other", "llm_error")


def test_signals_are_passed_to_the_model() -> None:
    gw = FakeGateway()
    classify_v2(_mail(subject="Re: wycena"), _mailbox(), _settings(), gw, _index(correspondents=["biuro@firma.pl"]))
    assert "pisał wcześniej do tego nadawcy: tak" in gw.calls[0]["signals"]
    assert "(Re:/Odp:): tak" in gw.calls[0]["signals"]


def test_hard_rule_reasons_exist_in_rule_engine() -> None:
    source = inspect.getsource(rule_engine)
    for reason in HARD_RULE_REASONS:
        assert f'"{reason}"' in source


def test_sent_index_roundtrip_and_own_address(tmp_path) -> None:
    ix = _index(sent_ids=["<a@b>"], correspondents=["Anna@X.pl", "kontakt@salon-bw.pl"])
    ix.save(tmp_path / "i.json")
    loaded = SentIndex.load(tmp_path / "i.json")
    assert loaded.is_reply_to_me("<a@b>", None)
    assert loaded.is_correspondent("Anna <anna@x.pl>")
    assert not loaded.is_correspondent("kontakt@salon-bw.pl")
    assert "anna" not in (tmp_path / "i.json").read_text()  # hashed, not plain addresses
    assert SentIndex.load(tmp_path / "missing.json").message_ids == set()


def test_shadow_writes_row_and_swallows_errors(tmp_path) -> None:
    shadow = ShadowV2(_settings(tmp_path))
    shadow.gateway = FakeGateway("szum", "newsletter")
    shadow.record(_mail(subject="Promo"), _mailbox(), "question", "f" * 40)
    shadow.gateway = FakeGateway(error=ValueError("bad json"))
    shadow.record(_mail(), _mailbox(), "system", "g" * 40)
    rows = [json.loads(line) for line in (tmp_path / "shadow.jsonl").read_text().splitlines()]
    assert rows[0]["v1"] == "question" and rows[0]["v2"] == "newsletter"
    assert rows[0]["inbox_v1"] is True and rows[0]["inbox_v2"] is False
    assert rows[1]["source"] == "llm_error"


@pytest.mark.parametrize("value", ["DO_ZROBIENIA", " do_zrobienia "])
def test_v2_payload_normalization(value) -> None:
    from mail_ai_agent.llm_gateway import _normalize_classification_payload

    raw = json.dumps({"action": value, "category": "Question", "confidence": "0.8", "reasoning_short": "r",
                      "summary": "dropped"})
    out = _normalize_classification_payload(raw, allowed_keys=set(LLMActionClassification.model_fields))
    assert LLMActionClassification.model_validate(out).action == "do_zrobienia"
