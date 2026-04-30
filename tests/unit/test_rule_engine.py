from __future__ import annotations

from pydantic import SecretStr

from mail_ai_agent.config import MailboxConfig
from mail_ai_agent.rule_engine import evaluate_rules
from mail_ai_agent.schemas import ParsedEmail


def make_mailbox(billing_payment_email: str | None = None) -> MailboxConfig:
    return MailboxConfig(
        mailbox_id="test",
        imap_host="imap.example.com",
        imap_user="u@example.com",
        imap_pass=SecretStr("secret"),
        billing_payment_email=billing_payment_email,
    )


def test_rule_engine_routes_billing_without_llm() -> None:
    parsed = ParsedEmail(sender="billing@example.com", subject="Faktura za marzec", normalized_body="")

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "billing"


def test_rule_engine_routes_payment_reminder_without_llm() -> None:
    parsed = ParsedEmail(
        sender="Obsługa Płatności T-Mobile <obslugaPlatnosci1@t-mobile.pl>",
        subject="Przypomnienie o terminie płatności",
        normalized_body="To jest przypomnienie o terminie płatności za usługę.",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "billing"


def test_rule_engine_routes_missed_payment_without_llm() -> None:
    parsed = ParsedEmail(
        sender="Obsługa Płatności T-Mobile <obslugaPlatnosci1@t-mobile.pl>",
        subject="Brak płatności w terminie",
        normalized_body="Informujemy o braku płatności w terminie i prosimy o uregulowanie należności.",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "billing"


def test_rule_engine_routes_system_without_llm() -> None:
    parsed = ParsedEmail(sender="mailer-daemon@example.com", subject="Delivery Status Notification", normalized_body="")

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "system"


def test_rule_engine_falls_back_to_llm() -> None:
    parsed = ParsedEmail(sender="client@example.com", subject="Pytanie o manicure", normalized_body="Czy sa wolne terminy?")

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "needs_llm"


def test_rule_engine_catches_marketing_pitch_disguised_as_question() -> None:
    parsed = ParsedEmail(
        sender="Klient Premium <contact@premium-growth.test>",
        subject="Pytanie o mozliwosc wspolpracy z salonem",
        normalized_body="Reprezentuje agencje marketingowa i chcialbym porozmawiac o wspolpracy SEO oraz reklamach dla salonu.",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "offer"


def test_rule_engine_catches_polish_inflections_for_marketing_offer() -> None:
    parsed = ParsedEmail(
        sender="Klient Premium 2 <contact2@premium-growth.test>",
        subject="Pytanie o mozliwosc wspolpracy reklamowej",
        normalized_body="Dzien dobry, reprezentuje agencje marketingowa i chcialbym porozmawiac o wspolpracy reklamowej, SEO i pozyskiwaniu leadow dla salonu.",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "offer"


def test_rule_engine_catches_newsletter_sender_without_llm() -> None:
    parsed = ParsedEmail(
        sender="Rituals Cosmetics <newsletter@c.rituals.com>",
        subject="Nowe wydanie Private Collection.",
        normalized_body="Specjalnie dla Ciebie. Kup teraz i sprawdz nowa kolekcje.",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "newsletter"


def test_rule_engine_routes_retail_newsletter_with_coupon_without_llm() -> None:
    parsed = ParsedEmail(
        sender="Sklep Beauty <promo@beauty-store.test>",
        subject="Nowa kolekcja i kod rabatowy na zakupy",
        normalized_body="Twoj kod rabatowy czeka. Wypisz sie z newslettera, jesli nie chcesz kolejnych wiadomosci.",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "newsletter"


def test_rule_engine_catches_marketing_audit_offer_without_llm() -> None:
    parsed = ParsedEmail(
        sender="Growth Lab <kontakt@growth-lab.test>",
        subject="Darmowy audyt SEO i Google Ads dla salonu",
        normalized_body="Przygotowalismy bezplatny audyt marketingowy, ktory pomoze zwiekszyc ruch i pozyskac wiecej klientow.",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "offer"


def test_rule_engine_catches_newsletter_unsubscribe_pattern_without_llm() -> None:
    parsed = ParsedEmail(
        sender="Beauty Platform <hello@beauty-platform.test>",
        subject="Nowa propozycja wspolpracy dla Twojego salonu",
        normalized_body="Jesli nie chcesz otrzymywac takich wiadomosci, kliknij wypisz. Oferujemy wsparcie social media i kampanie reklamowe.",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "offer"


def test_rule_engine_catches_obvious_spam_without_llm() -> None:
    parsed = ParsedEmail(
        sender="Prize Center <promo@spam.test>",
        subject="Claim your prize now",
        normalized_body="Act now to claim your prize and get a quick loan approved today.",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "spam"


def test_rule_engine_prioritizes_spam_over_newsletter_markers() -> None:
    parsed = ParsedEmail(
        sender="Prize Center <promo@spam.test>",
        subject="Unsubscribe and claim your prize",
        normalized_body="Act now, claim your prize, verify your account and unsubscribe here.",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "spam"


def test_rule_engine_keeps_customer_promo_question_for_llm() -> None:
    parsed = ParsedEmail(
        sender="Klientka <klientka@example.com>",
        subject="Pytanie o promocje na manicure",
        normalized_body="Dzien dobry, czy macie teraz jakas promocje na manicure hybrydowy dla nowych klientek?",
    )

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "needs_llm"


def test_category_to_folder_unknown_returns_source_folder():
    from mail_ai_agent.folder_mapper import category_to_folder

    mailbox = make_mailbox()
    assert category_to_folder("unknown_category", mailbox) == mailbox.imap_source_folder


def test_billing_email_from_config_triggers_billing_rule() -> None:
    mailbox = make_mailbox(billing_payment_email="billing@company.com")
    parsed = ParsedEmail(
        message_id=None, sender="billing@company.com", subject="Invoice",
        plain_text_body="", normalized_body="invoice billing@company.com", date=None,
    )
    decision = evaluate_rules(parsed, mailbox)
    assert decision.category == "billing"


def test_no_billing_email_in_config_does_not_raise() -> None:
    mailbox = make_mailbox()
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

    decision = evaluate_rules(parsed, make_mailbox())

    assert decision.action == "skip_ai"
    assert decision.category == "complaint"
