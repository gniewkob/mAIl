# Architecture

## Purpose

`mAIl` is a local multi-mailbox email triage worker. It reads mail from IMAP source folders, applies deterministic rules first, calls a local LLM only for ambiguous cases, routes mail to operational folders, and records state plus audit data locally.

Current deployment target:

- Mac mini
- Python worker
- Ollama
- SQLite
- JSONL audit log
- `launchd`
- IMAP mailboxes hosted on MyDevil

## Runtime flow

1. `launchd` starts the worker.
2. The worker loads settings and the active mailbox manifest.
3. One global worker lock is acquired.
4. Mailboxes are processed sequentially.
5. For each message:
   - parse and normalize email
   - compute fingerprint
   - acquire SQLite lease
   - apply deterministic rules
   - call Ollama only if needed
   - map result to folder and workflow action
   - in `DRY_RUN=false`, perform `copy -> delete_message`
6. Audit and state are updated.

## Current folder policy

- `spam -> Junk`
- `newsletter -> INBOX.Newsletter`
- `offer -> INBOX.Offer`
- `other -> INBOX.Other`
- `parse_error -> INBOX.AI-Uncertain`

## Current confidence policy

- most categories must meet `MOVE_CONFIDENCE_THRESHOLD`
- category `other` uses a lower `OTHER_MOVE_CONFIDENCE_THRESHOLD`
- this keeps low-signal general mail out of `INBOX.AI-Uncertain`
- `uncertain` is reserved for genuinely low-confidence decisions, parse failures, and operational fallback paths

## Core components

| Component | Responsibility |
| --- | --- |
| [config.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/config.py) | Settings, mailbox manifest, per-mailbox overrides |
| [email_parser.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/email_parser.py) | RFC822 parsing, normalization, attachment metadata, fingerprint inputs |
| [rule_engine.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/rule_engine.py) | Deterministic routing for billing, complaints, system mail, spam, newsletters, offers |
| [llm_gateway.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/llm_gateway.py) | Local Ollama classification with schema validation |
| [decision_engine.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/decision_engine.py) | Final workflow action and folder mapping |
| [imap_client.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/imap_client.py) | IMAP fetch, copy, delete, retry, runtime/preflight validation |
| [state_manager.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/state_manager.py) | SQLite workflow state, leases, cleanup tracking |
| [audit_logger.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/audit_logger.py) | Append-only JSONL audit trail |
| [metrics_exporter.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/metrics_exporter.py) | Prometheus metrics from state and audit data |
| [main.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/main.py) | Worker orchestration |

## Operational boundaries

- Deterministic rules protect high-risk categories before the model.
- LLM returns semantics; application code decides folder and workflow state.
- Runtime validation is lighter than preflight validation and should not act like deployment gating.
- `cleanup_pending` exists for partial IMAP success after copy succeeded but source deletion failed.

## State and observability

SQLite stores:

- mailbox-scoped uniqueness
- leases
- workflow status
- IMAP metadata
- cleanup state

JSONL audit stores:

- mailbox metadata
- category
- action taken
- target folder
- status transitions
- error context

Prometheus/Grafana expose:

- current state
- category distribution
- route source breakdown
- action breakdown
- parse error count
- event-based success metrics
