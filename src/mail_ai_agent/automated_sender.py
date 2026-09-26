"""Deterministic guard: automated senders are never 'human' mail.

The LLM classifies by topic ("reklamacja", "wizyta", "zamówienie"), so a no-reply shop, parking
operator or calendar notification ends up as complaint/appointment/other and stays in INBOX.
A message from an automated sender cannot need a personal reply, so the human categories are
remapped: bulk mail (List-* / Precedence headers) -> newsletter, other automated mail -> system.
Non-human categories chosen by the LLM (billing, offer, spam, ...) are kept as they are.
"""
from __future__ import annotations

import re
from email.utils import parseaddr

from .schemas import LLMClassification, ParsedEmail

HUMAN_CATEGORIES = {"appointment", "question", "complaint", "other"}

_AUTOMATED_LOCALPART = re.compile(
    r"(^|[._+-])(no-?reply|no_reply|do-?not-?reply|donotreply|noreply\d*|no-response|notifications?|"
    r"powiadomienia|newsletter|mailer-daemon|mailer|bounces?|automated|alerts?)([._+-]|$)",
    re.IGNORECASE,
)


def _header(parsed: ParsedEmail, name: str) -> str:
    return str(parsed.headers.get(name, "") or "").strip().lower()


def is_bulk(parsed: ParsedEmail) -> bool:
    return bool(
        _header(parsed, "list-unsubscribe")
        or _header(parsed, "list-id")
        or _header(parsed, "precedence") in {"bulk", "list", "junk"}
    )


def is_automated(parsed: ParsedEmail) -> bool:
    auto_submitted = _header(parsed, "auto-submitted")
    if auto_submitted and auto_submitted != "no":
        return True
    address = parseaddr(parsed.sender or "")[1].lower()
    local = address.split("@", 1)[0] if "@" in address else address
    return bool(_AUTOMATED_LOCALPART.search(local))


def _kept_by_allowlist(parsed: ParsedEmail, keep_senders: str) -> bool:
    address = parseaddr(parsed.sender or "")[1].lower()
    needles = [n.strip().lower() for n in (keep_senders or "").split(",") if n.strip()]
    return any(n in address for n in needles)


def apply_automated_sender_guard(
    classification: LLMClassification, parsed: ParsedEmail, keep_senders: str = ""
) -> LLMClassification:
    if classification.category not in HUMAN_CATEGORIES:
        return classification
    if _kept_by_allowlist(parsed, keep_senders):
        return classification
    bulk = is_bulk(parsed)
    if not (bulk or is_automated(parsed)):
        return classification
    target = "newsletter" if bulk else "system"
    return classification.model_copy(
        update={
            "category": target,
            "requires_reply": False,
            "draft_reply": None,
            "reasoning_short": f"{classification.reasoning_short} [guard: automated sender, "
            f"{classification.category}->{target}]",
        }
    )
