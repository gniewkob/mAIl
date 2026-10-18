"""Online shops: marketing -> Newsletter, order/shipment status -> Zakupy, order needing action -> INBOX."""
from __future__ import annotations

import pytest
from pydantic import SecretStr

from mail_ai_agent.config import MailboxConfig
from mail_ai_agent.folder_mapper import category_to_folder, target_folders
from mail_ai_agent.rule_engine import evaluate_rules
from mail_ai_agent.schemas import ParsedEmail


def _mb() -> MailboxConfig:
    return MailboxConfig(mailbox_id="g", imap_host="h", imap_user="g@bodora.pl", imap_pass=SecretStr("x"))


def _rule(sender: str, subject: str, body: str = ""):
    return evaluate_rules(ParsedEmail(sender=sender, subject=subject, normalized_body=body), _mb())


@pytest.mark.parametrize("sender,subject", [
    ("AliExpress <ae-best-care-market17@deals.aliexpress.com>", "Od 3 sztuk: żadne cło do zapłaty"),
    ("AliExpress <ae-ug-ut-interest17@mail.aliexpress.com>", "Przeliczyliśmy to"),
    ("\"Lounge by Zalando\" <message@lounge.zalando.pl>", "Kup Jimmy Choo z Plus Exclusive"),
    ("\"BESTSECRET\" <noreply@fashionnews.bestsecret.com>", "Mid Season Sale: -65% + extra -20% oferta"),
    ("Alibaba <alisourcepro@service.alibaba.com>", "Source large orders using RFQ"),
    ("Alibaba <noreply@order.alibaba.com>", "Ensure Quality: Procure with Verified Suppliers"),
    ("Alibaba <noreply@buynotice.alibaba.com>", "Hi Mscigniew, your next sourcing find"),
])
def test_shop_marketing_is_newsletter_even_with_payment_or_offer_words(sender, subject) -> None:
    rule = _rule(sender, subject, body="płatność oferta współpraca")
    assert (rule.category, rule.action) == ("newsletter", "skip_ai")


@pytest.mark.parametrize("sender,subject", [
    ("AliExpress <transaction@notice.aliexpress.com>", "Paczka 620999672942600439771918 została dostarczona"),
    ("AliExpress <transaction@notice.aliexpress.com>", "Zamówienie 3076683073645943: jak poszło?"),
    ("AliExpress <transaction@notice.aliexpress.com>", "Paczka 620999672942600439771918: w urzędzie celnym"),
    ("Zalando <info@service-mail.zalando.pl>", "Twoje zamówienie zostało wysłane"),
    ("BESTSECRET <noreply@email.bestsecret.com>", "Zamówienie 12345678 zostało wysłane"),
])
def test_order_and_shipment_status_goes_to_zakupy(sender, subject) -> None:
    rule = _rule(sender, subject)
    assert rule.category == "orders"
    assert rule.target_folder == "INBOX.Zakupy"


@pytest.mark.parametrize("subject", [
    "Zamówienie 3076683073665943: oczekiwanie na potwierdzenie",
    "Paczka 620999672942600439771918 jest gotowa do odbioru",
])
def test_order_needing_action_stays_human(subject) -> None:
    assert _rule("AliExpress <transaction@notice.aliexpress.com>", subject).category == "question"


def test_non_shop_mail_is_untouched() -> None:
    assert _rule("Sklep <sklep@lantre.pl>", "Twoje zamówienie 123456 zostało wysłane").category != "orders"


def test_zakupy_folder_is_a_routing_target() -> None:
    assert category_to_folder("orders", _mb()) == "INBOX.Zakupy"
    assert "INBOX.Zakupy" in target_folders(_mb())
