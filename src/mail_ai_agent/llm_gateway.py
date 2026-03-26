from __future__ import annotations

import json
import time

import requests

from .config import Settings
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

Dozwolone wartości:
- category: appointment, question, complaint, spam_or_offer, other
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

Treść wiadomości:
Nadawca: {sender}
Temat: {subject}
Data: {date}
Czy są załączniki: {has_attachments}
Treść:
{body}
"""


class LLMGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def classify(self, parsed_email: ParsedEmail) -> tuple[LLMClassification, int]:
        prompt = PROMPT_TEMPLATE.format(
            sender=parsed_email.sender,
            subject=parsed_email.subject,
            date=parsed_email.date.isoformat() if parsed_email.date else "",
            has_attachments=parsed_email.has_attachments,
            body=parsed_email.normalized_body,
        )
        last_error: Exception | None = None
        for _ in range(self.settings.max_retries):
            started = time.perf_counter()
            try:
                response = requests.post(
                    f"{self.settings.ollama_url}/api/generate",
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "format": "json",
                        "stream": False,
                        "options": {"temperature": self.settings.ollama_temperature},
                    },
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
                continue
        raise RuntimeError(f"LLM classification failed after retries: {last_error}") from last_error


def _extract_json(raw_output: str) -> str:
    raw_output = raw_output.strip()
    if raw_output.startswith("{") and raw_output.endswith("}"):
        return raw_output
    start = raw_output.find("{")
    end = raw_output.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output")
    return json.dumps(json.loads(raw_output[start : end + 1]), ensure_ascii=False)


def _normalize_classification_payload(raw_output: str) -> dict:
    payload = json.loads(_extract_json(raw_output))
    entities = payload.get("entities")
    if entities is None or entities == []:
        payload["entities"] = {}
    elif not isinstance(entities, dict):
        raise ValueError("entities must be an object or empty list")
    return payload
