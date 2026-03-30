from __future__ import annotations

from mail_ai_agent.config import Settings
from mail_ai_agent.rule_engine import evaluate_rules
from mail_ai_agent.schemas import ParsedEmail


def make_settings() -> Settings:
    return Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="user@example.com",
        IMAP_PASS="secret",
    )


def test_rule_engine_routes_billing_without_llm() -> None:
    parsed = ParsedEmail(sender="billing@example.com", subject="Faktura za marzec", normalized_body="")

    decision = evaluate_rules(parsed, make_settings())

    assert decision.action == "skip_ai"
    assert decision.category == "billing"


def test_rule_engine_routes_system_without_llm() -> None:
    parsed = ParsedEmail(sender="mailer-daemon@example.com", subject="Delivery Status Notification", normalized_body="")

    decision = evaluate_rules(parsed, make_settings())

    assert decision.action == "skip_ai"
    assert decision.category == "system"


def test_rule_engine_falls_back_to_llm() -> None:
    parsed = ParsedEmail(sender="client@example.com", subject="Pytanie o manicure", normalized_body="Czy sa wolne terminy?")

    decision = evaluate_rules(parsed, make_settings())

    assert decision.action == "needs_llm"


def test_rule_engine_catches_marketing_pitch_disguised_as_question() -> None:
    parsed = ParsedEmail(
        sender="Klient Premium <contact@premium-growth.test>",
        subject="Pytanie o mozliwosc wspolpracy z salonem",
        normalized_body="Reprezentuje agencje marketingowa i chcialbym porozmawiac o wspolpracy SEO oraz reklamach dla salonu.",
    )

    decision = evaluate_rules(parsed, make_settings())

    assert decision.action == "skip_ai"
    assert decision.category == "spam_or_offer"


def test_rule_engine_catches_polish_inflections_for_marketing_offer() -> None:
    parsed = ParsedEmail(
        sender="Klient Premium 2 <contact2@premium-growth.test>",
        subject="Pytanie o mozliwosc wspolpracy reklamowej",
        normalized_body="Dzien dobry, reprezentuje agencje marketingowa i chcialbym porozmawiac o wspolpracy reklamowej, SEO i pozyskiwaniu leadow dla salonu.",
    )

    decision = evaluate_rules(parsed, make_settings())

    assert decision.action == "skip_ai"
    assert decision.category == "spam_or_offer"


def test_category_to_folder_unknown_returns_source_folder():
    from mail_ai_agent.folder_mapper import category_to_folder
    from mail_ai_agent.config import MailboxConfig
    from pydantic import SecretStr

    mailbox = MailboxConfig(mailbox_id="test", imap_host="imap.example.com",
                            imap_user="u@example.com", imap_pass=SecretStr("secret"))
    assert category_to_folder("unknown_category", mailbox) == mailbox.imap_source_folder


def test_billing_email_from_config_triggers_billing_rule() -> None:
    from pydantic import SecretStr
    from mail_ai_agent.config import MailboxConfig
    from mail_ai_agent.schemas import ParsedEmail
    from mail_ai_agent.rule_engine import evaluate_rules

    mailbox = MailboxConfig(
        mailbox_id="test", imap_host="imap.example.com",
        imap_user="u@example.com", imap_pass=SecretStr("secret"),
        billing_payment_email="billing@company.com",
    )
    parsed = ParsedEmail(
        message_id=None, sender="billing@company.com", subject="Invoice",
        plain_text_body="", normalized_body="invoice billing@company.com", date=None,
    )
    decision = evaluate_rules(parsed, mailbox)
    assert decision.category == "billing"


def test_no_billing_email_in_config_does_not_raise() -> None:
    from pydantic import SecretStr
    from mail_ai_agent.config import MailboxConfig
    from mail_ai_agent.schemas import ParsedEmail
    from mail_ai_agent.rule_engine import evaluate_rules

    mailbox = MailboxConfig(
        mailbox_id="test", imap_host="imap.example.com",
        imap_user="u@example.com", imap_pass=SecretStr("secret"),
    )
    parsed = ParsedEmail(
        message_id=None, sender="x@y.com", subject="Hello",
        plain_text_body="", normalized_body="hello", date=None,
    )
    decision = evaluate_rules(parsed, mailbox)
    assert decision is not None


def test_rule_engine_does_not_confuse_reklamacja_with_marketing() -> None:
    parsed = ParsedEmail(
        sender="Joanna Reklamacja <joanna.reklamacja@example.com>",
        subject="Reklamacja po ostatniej wizycie",
        normalized_body="Dzien dobry, jestem niezadowolona z ostatniej uslugi i chce zlozyc reklamacje. Prosze o pilny kontakt.",
    )

    decision = evaluate_rules(parsed, make_settings())

    assert decision.action == "skip_ai"
    assert decision.category == "complaint"
