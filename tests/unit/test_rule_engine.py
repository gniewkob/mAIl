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


def test_rule_engine_routes_payment_reminder_without_llm() -> None:
    parsed = ParsedEmail(
        sender="Obsługa Płatności T-Mobile <obslugaPlatnosci1@t-mobile.pl>",
        subject="Przypomnienie o terminie płatności",
        normalized_body="To jest przypomnienie o terminie płatności za usługę.",
    )

    decision = evaluate_rules(parsed, make_settings())

    assert decision.action == "skip_ai"
    assert decision.category == "billing"


def test_rule_engine_routes_missed_payment_without_llm() -> None:
    parsed = ParsedEmail(
        sender="Obsługa Płatności T-Mobile <obslugaPlatnosci1@t-mobile.pl>",
        subject="Brak płatności w terminie",
        normalized_body="Informujemy o braku płatności w terminie i prosimy o uregulowanie należności.",
    )

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


def test_rule_engine_does_not_confuse_reklamacja_with_marketing() -> None:
    parsed = ParsedEmail(
        sender="Joanna Reklamacja <joanna.reklamacja@example.com>",
        subject="Reklamacja po ostatniej wizycie",
        normalized_body="Dzien dobry, jestem niezadowolona z ostatniej uslugi i chce zlozyc reklamacje. Prosze o pilny kontakt.",
    )

    decision = evaluate_rules(parsed, make_settings())

    assert decision.action == "skip_ai"
    assert decision.category == "complaint"
