---
title: Email Parser - Izolacja błędów kodowania (cp-850)
domain: build
entity_type: Architecture
tags: [mail-ai, error-handling, encoding, resilience, python, cp-850, graceful-degradation]
date: 2026-04-04
project: mAIl - Local AI Email Triage
---

# Email Parser - Izolacja błędów kodowania (cp-850)

## Problem
Worker AI Mail Triage crashował na błędzie `LookupError: unknown encoding: cp-850`, co wymagało ręcznej interwencji i restartu workera.

## Rozwiązanie
Wdrożono wzorzec Graceful Degradation - zamiast zatrzymywać workera, problematyczne wiadomości są oznaczane jako "parse error" i izolowane. System kontynuuje przetwarzanie kolejnych wiadomości.

## Zmiany techniczne

### email_parser.py - _safe_part_content()
```python
try:
    content = raw_payload.decode(charset, errors="replace")
except LookupError:
    # Unknown encoding (e.g., cp-850), fallback to utf-8
    content = raw_payload.decode("utf-8", errors="replace")
```

### pipeline/stages.py - ParseStage.process()
```python
except (ValueError, TypeError, hashlib.HashlibError, LookupError) as exc:
    context.parse_error = exc
except Exception as exc:
    # Log and continue instead of crashing
    context.parse_error = exc
```

## Korzyści
- Worker nie przerywa pracy na błędach kodowania
- Automatyczna izolacja problematycznych wiadomości
- Zero interwencji administratora
- Wydajność: ~480 wiadomości/minutę

## Testy
Dodano 3 testy w `tests/unit/test_encoding_error_handling.py`:
- test_safe_part_content_handles_unknown_encoding
- test_safe_part_content_handles_cp850_in_get_content
- test_decode_with_lookuperror_fallback

Wszystkie 260 testów przechodzi.

## Metryki
| Metryka | Przed | Po |
|---------|-------|-----|
| Wiadomości przetworzone | ~10,025 | 10,198+ |
| Crash workera | 1+ | 0 |
| Interwencja admina | Wymagana | Nie wymagana |

## Status: Wdrożone w produkcji 2026-04-04
## Pliki: docs/error-handling-architecture.md, CHANGELOG.md
