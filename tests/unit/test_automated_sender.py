from __future__ import annotations

from mail_ai_agent.automated_sender import apply_automated_sender_guard, is_automated, is_bulk
from mail_ai_agent.schemas import LLMClassification, ParsedEmail


def _parsed(sender: str, headers: dict[str, str] | None = None) -> ParsedEmail:
    return ParsedEmail(sender=sender, subject="s", headers=headers or {})


def _cls(category: str) -> LLMClassification:
    return LLMClassification.model_validate({
        "category": category, "priority": "low", "requires_reply": True, "confidence": 0.9,
        "summary": "x", "entities": {}, "draft_reply": "hi", "reasoning_short": "r",
    })


def test_noreply_complaint_becomes_system() -> None:
    out = apply_automated_sender_guard(_cls("complaint"), _parsed("APCOA <noreply+pez-apcoa-pl@email-rec.pl>"))
    assert out.category == "system"
    assert out.requires_reply is False and out.draft_reply is None


def test_bulk_appointment_becomes_newsletter() -> None:
    p = _parsed("Zara <shop@zara.com>", {"list-unsubscribe": "<mailto:u@zara.com>"})
    assert apply_automated_sender_guard(_cls("appointment"), p).category == "newsletter"


def test_non_human_llm_category_is_kept() -> None:
    p = _parsed("HQGSM <noreply@fakturownia.pl>")
    assert apply_automated_sender_guard(_cls("billing"), p).category == "billing"


def test_person_is_untouched() -> None:
    p = _parsed("Anna Kowalska <anna.kowalska@gmail.com>")
    assert apply_automated_sender_guard(_cls("appointment"), p).category == "appointment"


def test_localpart_substring_does_not_trigger() -> None:
    # 'mailer' / 'alert' must be whole tokens, not substrings of a person's name
    assert not is_automated(_parsed("Kasia <kasiamailerowa@gmail.com>"))
    assert is_automated(_parsed("x <no-reply@x.pl>"))
    assert is_automated(_parsed("x <powiadomienia@bank.pl>"))


def test_auto_submitted_header() -> None:
    assert is_automated(_parsed("a <a@b.pl>", {"auto-submitted": "auto-generated"}))
    assert not is_automated(_parsed("a <a@b.pl>", {"auto-submitted": "no"}))


def test_allowlist_keeps_booking_system() -> None:
    p = _parsed("Versum <noreply@versum.com>")
    assert apply_automated_sender_guard(_cls("appointment"), p, "versum").category == "appointment"


def test_is_bulk_precedence() -> None:
    assert is_bulk(_parsed("a <a@b.pl>", {"precedence": "bulk"}))


def test_rule_engine_does_not_spam_trusted_booking_sender() -> None:
    from mail_ai_agent.config import MailboxConfig
    from mail_ai_agent.rule_engine import evaluate_rules

    mb = MailboxConfig.model_validate({"mailbox_id": "m", "imap_host": "h", "imap_user": "u@x.pl",
                                       "imap_pass": "p", "imap_source_folder": "INBOX",
                                       "automated_sender_keep": "booksy"})
    p = ParsedEmail(sender="Booksy <no-reply@booksy.com>", subject="Anna: nowa rezerwacja piątek",
                    headers={"x-spam-status": "Yes, score=6.5", "x-spam-flag": "YES"})
    assert evaluate_rules(p, mb).action == "needs_llm"
    spoof = ParsedEmail(sender="x <no-reply@booksy.com.evil.ru>", subject="s", headers={"x-spam-flag": "YES"})
    assert evaluate_rules(spoof, mb).category == "spam"
    llm = apply_automated_sender_guard(_cls("appointment"), p, "booksy")
    assert llm.category == "appointment"
