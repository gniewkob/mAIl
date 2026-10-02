"""Classifier v2: "does this need the owner's action?" decides routing; the type is only a tag.

Cascade (first match wins):
  1. protective signals  - reply to a message the owner sent -> human; booking notification -> appointment
  2. hard rules          - spam headers, bank documents, bounces, complaints (rule_engine, content-independent)
  3. bulk + content rule - newsletter/offer/billing keyword rules only when the mail is bulk (List-* headers)
  4. LLM (action axis)   - action + type + confidence, with the signals above passed as context
  5. floors              - known correspondent never becomes newsletter/offer/spam/system;
                           a noise verdict below CLASSIFIER_V2_NOISE_MIN_CONFIDENCE stays in INBOX ("other")
The output is a v1 category, so hybrid routing works unchanged: human categories stay in INBOX.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr

from .automated_sender import HUMAN_CATEGORIES, is_bulk
from .config import MailboxConfig, Settings
from .llm_gateway import LLMGateway
from .rule_engine import REPLY_PREFIX_REGEX, evaluate_rules
from .schemas import LLMActionClassification, ParsedEmail
from .sent_index import SentIndex

NOISE_FLOORED = {"newsletter", "offer", "spam", "system"}
# rule_engine reasons that do not depend on guessing the topic from keywords
HARD_RULE_REASONS = {
    "SpamSieve header matched spam",
    "bank document matched in subject",
    "system pattern matched",
}  # complaint regex is keyword-based: shop footers ("regulamin reklamacji") hit it, so the LLM decides
BOOKING_REGEX = re.compile(
    r"(nowa rezerwacja|zmian\w* w rezerwacji|rezerwacja online|odwoła\w* (wizyt|rezerwacj)\w*|"
    r"anulowa\w* (wizyt|rezerwacj)\w*|przełożon\w* wizyt\w*)",
    re.IGNORECASE,
)

# Support/helpdesk reply to a ticket the owner opened via a web form (no Message-ID of ours to match)
TICKET_REGEX = re.compile(r"(\[#\s?\d{5,}\]|\[[a-z]{2,10}#\s?\d{5,}\]|ticket\s?#\s?\d{4,}|zg[łl]oszeni\w* nr\.?\s?\d{4,})",
                          re.IGNORECASE)

PROMPT_V2 = """Pomagasz właścicielowi skrzynek pocztowych zdecydować, które wiadomości wymagają jego uwagi.
Właściciel prowadzi małe firmy (m.in. salon usługowy z rezerwacjami przez Booksy) i ma pocztę prywatną.
Najważniejsze pytanie: czy ta wiadomość wymaga DZIAŁANIA odbiorcy?

unsolicited_offer: true, jeśli nadawca próbuje coś sprzedać lub zaproponować usługę, o którą odbiorca
nie prosił (SEO, pozycjonowanie, opinie Google, reklama, strony www, finansowanie, faktoring, pożyczki,
energia, gadżety, "współpraca"), także gdy pisze imiennie, zadaje pytanie albo ma "Re:" w temacie.
false dla odpowiedzi na zapytanie odbiorcy, faktur za usługi, z których korzysta, i wiadomości od klientów.

action (wybierz jedno):
- "do_zrobienia": odbiorca musi osobiście coś zrobić w sprawie, którą sam prowadzi: odpowiedzieć
  konkretnej osobie lub firmie w toczącej się sprawie, dostarczyć dokument/zdjęcia/dane, o które go
  poproszono, potwierdzić zamówienie lub termin, zareagować w swoim zgłoszeniu (ticket), podjąć decyzję.
  Także pytania i prośby klientów, odpowiedzi supportu w zgłoszeniach odbiorcy oraz pisma z urzędów
  i instytucji publicznych (urząd skarbowy, ZUS, e-recepta, sąd, ubezpieczyciel). Automatyczny nadawca (no-reply) nie wyklucza tej opcji,
  jeśli bez działania odbiorcy sprawa utknie (np. zamówienie czeka na potwierdzenie, ubezpieczyciel czeka na zdjęcia).
- "informacja": dotyczy odbiorcy, ale jest rutynowa i obsługuje ją on w folderach, nie od razu.
- "szum": treść masowa: newsletter, promocja, reklama, powiadomienie marketingowe.
- "spam": oszustwo, phishing, podszywanie się pod bank lub kuriera.

To NIE jest "do_zrobienia", nawet jeśli wiadomość wzywa do działania:
- niezamówiona oferta lub cold outreach (SEO, pozycjonowanie, opinie Google, reklama, strony www,
  finansowanie, faktoring, pożyczki, energia, gadżety), także imienna, z pytaniem lub "Re:" w temacie,
  gdy właściciel nigdy nie pisał do nadawcy -> action "spam", category "spam";
- faktury, rachunki, rozliczenia i dokumenty od księgowej, wezwania do zapłaty, informacje o zadłużeniu,
  nieudane odnowienia subskrypcji -> action "informacja", category "billing";
- kody weryfikacyjne, potwierdzenia rejestracji, statusy zamówień, paczek i zwrotów (również
  "gotowa do odbioru"), prośby o ocenę, powiadomienia o koncie -> action "informacja", category "system".
W pozostałych wątpliwych przypadkach między "do_zrobienia" a "informacja" wybierz "do_zrobienia".

category (typ wiadomości, tylko do opisu):
appointment (rezerwacja, wizyta, termin spotkania), question (sprawa wymagająca działania),
complaint (reklamacja, skarga klienta), billing (faktury, płatności, bank), system (automatyczne
powiadomienia serwisów i kont), newsletter (mailing, promocje sklepów), offer (niezamówiona
propozycja handlowa od realnej firmy), spam, other (korespondencja osobista bez działania).

confidence: pewność wyboru action (0.0-1.0). reasoning_short: jedno zdanie po polsku.
Nie opieraj decyzji na podpisie, stopce ani starej cytowanej części wątku.
Zwróć wyłącznie JSON.

Sygnały techniczne: {signals}

<email_content>
Nadawca: {sender}
Temat: {subject}
Data: {date}
Czy są załączniki: {has_attachments}
Treść:
{body}
</email_content>
"""


class ActionGateway(LLMGateway):
    prompt_template = PROMPT_V2
    result_model = LLMActionClassification

    def _model(self) -> str:
        return self.settings.classifier_v2_model or self.settings.ollama_model

    def _think(self) -> bool | None:
        return self.settings.classifier_v2_think


@dataclass(frozen=True)
class V2Decision:
    category: str
    source: str  # protect | rule | bulk_rule | llm | floor_correspondent | floor_confidence | llm_error
    reason: str
    action: str | None = None
    confidence: float | None = None


def _domain(address: str) -> str:
    address = parseaddr(address or "")[1].lower()
    return address.rsplit("@", 1)[-1] if "@" in address else ""


def is_booking(parsed: ParsedEmail, mailbox: MailboxConfig) -> bool:
    if not BOOKING_REGEX.search(parsed.subject or "") or REPLY_PREFIX_REGEX.match(parsed.subject or ""):
        return False
    sender_domain = _domain(parsed.sender)
    own_domain = _domain(mailbox.imap_user)
    trusted = [t.strip().lower().lstrip("@") for t in (mailbox.automated_sender_keep or "").split(",") if t.strip()]
    trusted = [t if "." in t else f"{t}.com" for t in trusted]
    return bool(sender_domain) and (
        sender_domain == own_domain or any(sender_domain == t or sender_domain.endswith("." + t) for t in trusted)
    )


def _signals_text(reply_to_me: bool, correspondent: bool, bulk: bool, reply_prefix: bool) -> str:
    yn = {True: "tak", False: "nie"}
    return (f"odpowiedź na wiadomość wysłaną przez właściciela: {yn[reply_to_me]}; "
            f"właściciel pisał wcześniej do tego nadawcy: {yn[correspondent]}; "
            f"temat wygląda na odpowiedź (Re:/Odp:): {yn[reply_prefix]}; "
            f"masowa wysyłka (List-Unsubscribe/List-Id): {yn[bulk]}")


def protective_decision(parsed: ParsedEmail, mailbox: MailboxConfig, index: SentIndex) -> V2Decision | None:
    """Deterministic 'this is for the owner' signals. Used by classify_v2 and, with
    CLASSIFIER_V2_PROTECT=true, by production ahead of rule_engine and the v1 LLM."""
    headers = {str(k).lower(): str(v) for k, v in (parsed.headers or {}).items()}
    if index.is_reply_to_me(headers.get("in-reply-to"), headers.get("references")):
        return V2Decision("question", "protect", "reply to a message the owner sent")
    if is_booking(parsed, mailbox):
        return V2Decision("appointment", "protect", "booking notification from own/trusted domain")
    subject = parsed.subject or ""
    if REPLY_PREFIX_REGEX.match(subject) and TICKET_REGEX.search(subject) and not is_bulk(parsed):
        return V2Decision("question", "protect", "reply in a support ticket")
    return None


def classify_v2(parsed: ParsedEmail, mailbox: MailboxConfig, settings: Settings,
                gateway: ActionGateway, index: SentIndex) -> V2Decision:
    protected = protective_decision(parsed, mailbox, index)
    if protected is not None:
        return protected

    bulk = is_bulk(parsed)
    rule = evaluate_rules(parsed, mailbox)
    if rule.action != "needs_llm":
        if rule.reason in HARD_RULE_REASONS:
            return V2Decision(rule.category, "rule", rule.reason)
        if bulk:
            return V2Decision(rule.category, "bulk_rule", rule.reason)

    correspondent = index.is_correspondent(parsed.sender)
    signals = _signals_text(False, correspondent, bulk, bool(REPLY_PREFIX_REGEX.match(parsed.subject or "")))
    try:
        result, _ = gateway.classify(parsed, signals=signals)
    except Exception as exc:  # the worker must keep the mail in INBOX on any model failure
        return V2Decision("other", "llm_error", f"{type(exc).__name__}")

    # An offer the model still thinks needs action is ambiguous (a new customer writing "Termin"): let the
    # action decide. Bulk offers go to newsletter, not spam: Junk is reported to the server's spam filter.
    if result.unsolicited_offer and not correspondent and result.action != "do_zrobienia":
        return V2Decision("newsletter" if bulk else "spam", "llm_offer",
                          "unsolicited offer from a sender the owner never wrote to", result.action, result.confidence)
    if result.action == "spam":
        category = "spam"
    elif result.action == "do_zrobienia":
        category = result.category if result.category in HUMAN_CATEGORIES else "question"
    else:
        category = result.category
    if category in NOISE_FLOORED and correspondent and not bulk:
        return V2Decision("question", "floor_correspondent", "known correspondent is never noise",
                          result.action, result.confidence)
    if category not in HUMAN_CATEGORIES and result.confidence < settings.classifier_v2_noise_min_confidence:
        return V2Decision("other", "floor_confidence", f"{category} below noise confidence threshold",
                          result.action, result.confidence)
    return V2Decision(category, "llm", result.reasoning_short, result.action, result.confidence)
