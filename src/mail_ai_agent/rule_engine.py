from __future__ import annotations

import functools
import re

from .config import MailboxConfig
from .folder_mapper import category_to_folder
from .schemas import ParsedEmail, RuleDecision

BILLING_KEYWORDS = ("faktura", "invoice", "fv", "proforma")
PAYMENT_REGEX = re.compile(
    r"\b("
    r"płatno(?:ść|ści|sci|scią|sci[aą])|"
    r"termin(?:ie)? płatno(?:ści|sci)|"
    r"brak płatno(?:ści|sci)|"
    r"przypomnienie o terminie płatno(?:ści|sci)|"
    r"rozliczen\w*|"
    r"rachun\w*|"
    r"opłat\w*|"
    r"należno(?:ść|ści|sci)|"
    r"platnosci@swiatlowodem\.pl|"
    r"obslugaplatnosci"
    r")\b",
    flags=re.IGNORECASE,
)
SYSTEM_PATTERNS = ("mailer-daemon", "delivery status notification", "failure notice", "postmaster")
COMPLAINT_REGEX = re.compile(
    r"\b("
    r"reklamacj\w*|"
    r"niezadowolon\w*|"
    r"nieudanej usludze|"
    r"prosz[eę] o pilny kontakt z wlascicielem|"
    r"prosz[eę] o kontakt z wlascicielem|"
    r"efekt uslugi jest inny niz oczekiwany"
    r")\b",
    flags=re.IGNORECASE,
)
MARKETING_REGEX = re.compile(
    r"\b("
    r"seo|"
    r"leadgen|lead generation|pozyskiwani[ea] lead[oó]w?|"
    r"marketing\w*|marketing automation|"
    r"ads|reklam(y|a|owa|owej|owe|owych)?|kampani\w*|"
    r"cooperation|offer|ofert[ay]|"
    r"wsp[oó]łprac\w*|"
    r"agencj\w*|"
    r"reprezentuj\w* agencj\w*"
    r")\b",
    flags=re.IGNORECASE,
)


@functools.lru_cache(maxsize=64)
def _billing_email_pattern(billing_email: str | None) -> "re.Pattern[str] | None":
    if not billing_email:
        return None
    return re.compile(re.escape(billing_email), flags=re.IGNORECASE)


def evaluate_rules(parsed_email: ParsedEmail, mailbox: MailboxConfig) -> RuleDecision:
    subject = parsed_email.subject.lower()
    sender = parsed_email.sender.lower()
    body = parsed_email.normalized_body.lower()
    combined = " ".join([subject, sender, body])

    if any(keyword in subject for keyword in BILLING_KEYWORDS):
        return RuleDecision(
            category="billing",
            target_folder=category_to_folder("billing", mailbox),
            action="skip_ai",
            reason="billing keyword matched in subject",
        )

    if PAYMENT_REGEX.search(combined):
        return RuleDecision(
            category="billing",
            target_folder=category_to_folder("billing", mailbox),
            action="skip_ai",
            reason="payment or billing pattern matched",
        )

    if any(pattern in combined for pattern in SYSTEM_PATTERNS):
        return RuleDecision(
            category="system",
            target_folder=category_to_folder("system", mailbox),
            action="skip_ai",
            reason="system pattern matched",
        )

    if COMPLAINT_REGEX.search(combined):
        return RuleDecision(
            category="complaint",
            target_folder=category_to_folder("complaint", mailbox),
            action="skip_ai",
            requires_flag=True,
            reason="complaint pattern matched",
        )

    if MARKETING_REGEX.search(combined):
        return RuleDecision(
            category="spam_or_offer",
            target_folder=category_to_folder("other", mailbox),
            action="skip_ai",
            reason="marketing or outreach pattern matched",
        )

    billing_pat = _billing_email_pattern(getattr(mailbox, "billing_payment_email", None))
    if billing_pat and billing_pat.search(combined):
        return RuleDecision(
            category="billing",
            target_folder=category_to_folder("billing", mailbox),
            action="skip_ai",
            reason="billing payment email matched",
        )

    return RuleDecision(
        category="unknown",
        target_folder=mailbox.imap_source_folder,
        action="needs_llm",
        reason="no deterministic rule matched",
    )
