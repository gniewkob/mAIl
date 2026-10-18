from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from typing import Any

import requests  # type: ignore[import-untyped]
from pydantic import BaseModel

from .circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from .config import Settings
from .schemas import LLMClassification, ParsedEmail

LOGGER = logging.getLogger(__name__)


def _escape_format_braces(s: str) -> str:
    return s.replace("{", "{{").replace("}", "}}")

PROMPT_TEMPLATE = """Jesteś systemem klasyfikacji poczty dla firmy usługowej. Analizujesz pojedynczą wiadomość e-mail i zwracasz wyłącznie poprawny JSON.

Zasady:
1. Odpowiadasz wyłącznie JSON-em.
2. Nie dodajesz markdown, komentarzy, wyjaśnień ani tekstu poza JSON.
3. Nie zgadujesz faktów, których nie ma w wiadomości.
4. Jeśli wiadomość jest niejednoznaczna, obniż confidence.
5. Nie opieraj klasyfikacji na podpisie, disclaimerze ani starej części cytowanego wątku.
6. Jeśli wiadomość wygląda na typowy spam, scam, phishing, śmieciową lub podejrzaną wiadomość, ustaw kategorię "spam" nawet jeśli zawiera link wypisu lub język promocyjny.
7. Jeśli wiadomość wygląda na masowy newsletter sklepu, mailing subskrypcyjny, premierę kolekcji, kod rabatowy lub retail promocję do konsumenta, ustaw kategorię "newsletter".
8. Jeśli wiadomość wygląda na ofertę handlową, cold outreach, propozycję współpracy B2B, SEO, Ads, lead generation lub outreach agencji, ustaw kategorię "offer".
9. Nie klasyfikuj newslettera sklepu ani promocji detalicznej jako "offer".
10. Nie klasyfikuj spamu/scamu jako "newsletter" tylko dlatego, że zawiera unsubscribe lub treść promocyjną.
11. Draft reply twórz tylko wtedy, gdy wiadomość wymaga odpowiedzi i masz wysoką pewność.
12. Jeśli nie jesteś pewien, ustaw draft_reply na null.
13. Podsumowanie ma być krótkie, konkretne i po polsku.
14. reasoning_short ma mieć jedno zdanie.
15. Zacznij odpowiedź bezpośrednio od otwierającego nawiasu klamrowego JSON — bez wstępu, bez cytowania treści wiadomości.

Dozwolone wartości:
- category: appointment, question, complaint, spam, newsletter, offer, billing, system, other
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
    prompt_template: str = PROMPT_TEMPLATE
    result_model: type[BaseModel] = LLMClassification

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._circuit_breaker = CircuitBreaker(
            name="ollama_llm",
            failure_threshold=settings.ollama_circuit_breaker_failures,
            timeout_seconds=settings.ollama_circuit_breaker_timeout_seconds,
            expected_exception=(requests.RequestException, ValueError),
        )

    def _model(self) -> str:
        return self.settings.ollama_model

    def _think(self) -> bool | None:
        return self.settings.ollama_think

    def _prompt_fields(self, parsed_email: ParsedEmail, **extra: str) -> dict[str, object]:
        return {
            "sender": _escape_format_braces(parsed_email.sender),
            "subject": _escape_format_braces(parsed_email.subject),
            "date": parsed_email.date.isoformat() if parsed_email.date else "",
            "has_attachments": parsed_email.has_attachments,
            "body": _escape_format_braces(parsed_email.normalized_body),
            **extra,
        }

    def classify(self, parsed_email: ParsedEmail, **extra: str) -> tuple[Any, int]:
        prompt = self.prompt_template.format(**self._prompt_fields(parsed_email, **extra))
        
        # Check circuit breaker first for fast failure
        if self._circuit_breaker.state.value == "open":
            raise CircuitBreakerOpenError(
                f"LLM circuit breaker is OPEN. Ollama appears to be down. "
                f"Will retry in {self.settings.ollama_circuit_breaker_timeout_seconds}s."
            )
        
        result_model = self.result_model
        schema = None if result_model is LLMClassification else _schema_for(result_model)

        def _do_classify() -> tuple[Any, int]:
            started = time.perf_counter()
            raw_output: str = ""
            last_error: Exception | None = None
            
            for attempt in range(1, self.settings.max_retries + 1):
                try:
                    response = requests.post(
                        f"{self.settings.ollama_url}/api/generate",
                        json=_build_generate_payload(
                            model=self._model(),
                            prompt=prompt,
                            temperature=self.settings.ollama_temperature,
                            num_predict=self.settings.ollama_num_predict,
                            think=self._think(),
                            **({"schema": schema} if schema is not None else {}),
                        ),
                        timeout=self.settings.ollama_timeout_seconds,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    raw_output = payload.get("response", "")
                    classification = result_model.model_validate(
                        _normalize_classification_payload(raw_output, allowed_keys=set(result_model.model_fields))
                    )
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    return classification, latency_ms
                except (requests.RequestException, ValueError) as exc:
                    last_error = exc
                    # Do NOT log raw LLM output as it may contain sensitive email content.
                    # We only log the length for debugging connectivity/response issues.
                    LOGGER.debug(
                        "LLM output parsing failed (attempt %d, len=%d)",
                        attempt,
                        len(raw_output),
                    )
                    if attempt < self.settings.max_retries:
                        time.sleep(min(0.5 * attempt, 5.0))
                    continue
            raise RuntimeError(f"LLM classification failed after retries: {last_error}") from last_error
        
        try:
            return self._circuit_breaker.call(_do_classify)
        except CircuitBreakerOpenError:
            # Circuit breaker errors should be propagated as-is for proper handling
            raise


def _build_generate_payload(
    *, model: str, prompt: str, temperature: float, num_predict: int = 2048, think: bool | None = None,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # num_predict caps generation: on 2026-09-26 one qwen3 request ran away past 12k tokens
    # (~15 min) and blocked every other Ollama call. A truncated answer fails JSON validation,
    # is retried and ends up as uncertain (message stays in INBOX) instead of hanging the worker.
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "format": schema if schema is not None else _classification_json_schema(),
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if think is not None:
        payload["think"] = think
    return payload


@lru_cache(maxsize=1)
def _classification_json_schema() -> dict[str, Any]:
    schema = LLMClassification.model_json_schema()
    schema.pop("title", None)
    return schema


@lru_cache(maxsize=8)
def _schema_for(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
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


def _normalize_classification_payload(raw_output: str, allowed_keys: set[str] | None = None) -> dict[str, object]:
    payload = json.loads(_extract_json(raw_output))
    if not isinstance(payload, dict):
        raise ValueError("LLM payload must be a JSON object")
    if "classification" in payload and isinstance(payload["classification"], dict):
        payload = payload["classification"]
    elif "result" in payload and isinstance(payload["result"], dict):
        payload = payload["result"]
    if not isinstance(payload, dict):
        raise ValueError("LLM payload must normalize to a JSON object")

    allowed_keys = allowed_keys or {
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
    for key in ("category", "action"):
        if key in payload and isinstance(payload[key], str):
            payload[key] = payload[key].strip().lower()

    payload = {key: value for key, value in payload.items() if key in allowed_keys}
    if "entities" not in allowed_keys:
        return payload
    entities = payload.get("entities")
    if entities is None or entities == []:
        payload["entities"] = {}
    elif not isinstance(entities, dict):
        raise ValueError("entities must be an object or empty list")
    return payload
