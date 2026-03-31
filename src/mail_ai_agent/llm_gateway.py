from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from typing import Any

import requests  # type: ignore[import-untyped]

LOGGER = logging.getLogger(__name__)

from .config import Settings


def _escape_format_braces(s: str) -> str:
    return s.replace("{", "{{").replace("}", "}}")
from .schemas import LLMClassification, ParsedEmail

PROMPT_TEMPLATE = """Jesteś systemem klasyfikacji poczty dla firmy usługowej. Analizujesz pojedynczą wiadomość e-mail i zwracasz wyłącznie poprawny JSON.

Zasady:
1. Odpowiadasz wyłącznie JSON-em.
2. Nie dodajesz markdown, komentarzy, wyjaśnień ani tekstu poza JSON.
3. Nie zgadujesz faktów, których nie ma w wiadomości.
4. Jeśli wiadomość jest niejednoznaczna, obniż confidence.
5. Nie opieraj klasyfikacji na podpisie, disclaimerze ani starej części cytowanego wątku.
6. Jeśli wiadomość wygląda na spam, ofertę handlową lub cold outreach, ustaw kategorię "spam_or_offer".
7. Draft reply twórz tylko wtedy, gdy wiadomość wymaga odpowiedzi i masz wysoką pewność.
8. Jeśli nie jesteś pewien, ustaw draft_reply na null.
9. Podsumowanie ma być krótkie, konkretne i po polsku.
10. reasoning_short ma mieć jedno zdanie.
11. Zacznij odpowiedź bezpośrednio od otwierającego nawiasu klamrowego JSON — bez wstępu, bez cytowania treści wiadomości.

Dozwolone wartości:
- category: appointment, question, complaint, spam_or_offer, billing, system, other
- priority: high, medium, low

Wymagane pola JSON:
- category
- priority
- requires_reply
- confidence
- summary
- entities
- draft_reply
- reasoning_short

<email_content>
Nadawca: {sender}
Temat: {subject}
Data: {date}
Czy są załączniki: {has_attachments}
Treść:
{body}
</email_content>
"""


class LLMGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def classify(self, parsed_email: ParsedEmail) -> tuple[LLMClassification, int]:
        prompt = PROMPT_TEMPLATE.format(
            sender=_escape_format_braces(parsed_email.sender),
            subject=_escape_format_braces(parsed_email.subject),
            date=parsed_email.date.isoformat() if parsed_email.date else "",
            has_attachments=parsed_email.has_attachments,
            body=_escape_format_braces(parsed_email.normalized_body),
        )
        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            started = time.perf_counter()
            raw_output: str = ""
            try:
                response = requests.post(
                    f"{self.settings.ollama_url}/api/generate",
                    json=_build_generate_payload(
                        model=self.settings.ollama_model,
                        prompt=prompt,
                        temperature=self.settings.ollama_temperature,
                    ),
                    timeout=self.settings.ollama_timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                raw_output = payload.get("response", "")
                classification = LLMClassification.model_validate(_normalize_classification_payload(raw_output))
                latency_ms = int((time.perf_counter() - started) * 1000)
                return classification, latency_ms
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                LOGGER.debug("LLM raw output on failure (attempt %d): %s", attempt, raw_output)
                if attempt < self.settings.max_retries:
                    time.sleep(min(0.5 * attempt, 5.0))
                continue
        raise RuntimeError(f"LLM classification failed after retries: {last_error}") from last_error


def _build_generate_payload(*, model: str, prompt: str, temperature: float) -> dict[str, Any]:
    return {
        "model": model,
        "prompt": prompt,
        "format": _classification_json_schema(),
        "stream": False,
        "options": {"temperature": temperature},
    }


@lru_cache(maxsize=1)
def _classification_json_schema() -> dict[str, Any]:
    schema = LLMClassification.model_json_schema()
    schema.pop("title", None)
    return schema


def _extract_json(raw_output: str) -> str:
    raw_output = raw_output.strip()
    start = raw_output.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output")
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(raw_output[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.dumps(json.loads(raw_output[start : i + 1]), ensure_ascii=False)
    raise ValueError("No complete JSON object found in model output")


def _normalize_classification_payload(raw_output: str) -> dict[str, object]:
    payload = json.loads(_extract_json(raw_output))
    if not isinstance(payload, dict):
        raise ValueError("LLM payload must be a JSON object")
    if "classification" in payload and isinstance(payload["classification"], dict):
        payload = payload["classification"]
    elif "result" in payload and isinstance(payload["result"], dict):
        payload = payload["result"]
    if not isinstance(payload, dict):
        raise ValueError("LLM payload must normalize to a JSON object")

    allowed_keys = {
        "category",
        "priority",
        "requires_reply",
        "confidence",
        "summary",
        "entities",
        "draft_reply",
        "reasoning_short",
    }
    payload = dict(payload)
    if "reasoning_short" not in payload:
        alias_reasoning = payload.get("reasoning")
        if isinstance(alias_reasoning, str) and alias_reasoning.strip():
            payload["reasoning_short"] = alias_reasoning.strip()
        elif isinstance(payload.get("summary"), str) and str(payload["summary"]).strip():
            payload["reasoning_short"] = f"Klasyfikacja na podstawie treści wiadomości: {str(payload['summary']).strip()}"
    if "draft_reply" not in payload:
        payload["draft_reply"] = None
    if "requires_reply" in payload and isinstance(payload["requires_reply"], str):
        normalized = payload["requires_reply"].strip().lower()
        if normalized in {"true", "yes", "1"}:
            payload["requires_reply"] = True
        elif normalized in {"false", "no", "0"}:
            payload["requires_reply"] = False
    if "confidence" in payload and isinstance(payload["confidence"], str):
        try:
            payload["confidence"] = float(payload["confidence"])
        except ValueError:
            pass
    if "priority" in payload and isinstance(payload["priority"], str):
        payload["priority"] = payload["priority"].strip().lower()
    if "category" in payload and isinstance(payload["category"], str):
        payload["category"] = payload["category"].strip().lower()

    payload = {key: value for key, value in payload.items() if key in allowed_keys}
    entities = payload.get("entities")
    if entities is None or entities == []:
        payload["entities"] = {}
    elif not isinstance(entities, dict):
        raise ValueError("entities must be an object or empty list")
    return payload
