# Architecture

## Purpose

This project is a local multi-mailbox email triage worker. It reads emails from IMAP, applies deterministic rules first, optionally calls a local LLM, persists workflow state locally, and records an audit trail.

The current deployment target is a Mac mini running:

- Python worker
- Ollama locally
- SQLite locally
- JSONL audit log locally
- `launchd` for scheduling
- IMAP mailboxes hosted on MyDevil

## High-level model

The system is intentionally simple:

1. `launchd` starts the worker every 5 minutes.
2. The worker loads global settings and the active mailbox manifest.
3. The worker acquires one global runtime lock.
4. The worker processes configured mailboxes sequentially.
5. For each message:
   - parse email
   - compute fingerprint
   - acquire mailbox-scoped lease in SQLite
   - apply deterministic rules
   - if needed, call local Ollama
   - compute final routing decision
   - in `DRY_RUN=true`, only simulate
   - in `DRY_RUN=false`, perform `copy -> delete_message` IMAP routing
6. The worker writes audit entries and updates SQLite state.

## Main components

### Config

Files:

- [`src/mail_ai_agent/config.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/config.py)
- [`config/mailboxes.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.json)
- [`config/mailboxes.active.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json)

Responsibilities:

- global settings from `.env`
- mailbox manifest loading
- single-mailbox backward compatibility
- mailbox-specific overrides for folders and credentials

The active runtime model is:

- global settings control LLM, thresholds, state paths, worker id
- mailbox config controls IMAP login and folder mapping

### Parser and fingerprinting

Files:

- [`src/mail_ai_agent/email_parser.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/email_parser.py)

Responsibilities:

- parse raw RFC822 email
- extract text/plain and text/html
- normalize body
- remove quoted thread, signatures, disclaimers
- capture attachment metadata
- compute a stable fingerprint

Fingerprint input currently includes:

- `Message-ID`
- message date
- sender
- subject
- normalized body

This was expanded after real collisions on transactional emails.

### Rule engine

Files:

- [`src/mail_ai_agent/rule_engine.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/rule_engine.py)
- [`src/mail_ai_agent/folder_mapper.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/folder_mapper.py)

Responsibilities:

- catch deterministic cases before LLM
- classify billing/system/complaint/marketing cases
- reduce LLM usage
- improve predictability for high-risk categories

Important design choice:

- complaint and B2B outreach are intentionally protected by rules before the model
- billing and payment reminders are also protected by deterministic rules to avoid routing them into customer-intent folders

### LLM gateway

Files:

- [`src/mail_ai_agent/llm_gateway.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/llm_gateway.py)

Responsibilities:

- send one email at a time to local Ollama
- request strict JSON response
- validate response with Pydantic
- normalize known bad-but-recoverable shapes like `entities=[]`
- retry on temporary failures

Current model:

- `qwen3:8b`

### Decision engine

Files:

- [`src/mail_ai_agent/decision_engine.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/decision_engine.py)

Responsibilities:

- translate rule or LLM classification into final action
- keep folder mapping deterministic
- enforce uncertainty threshold
- generate flag and draft decisions

Important boundary:

- LLM returns semantics
- application computes target folder and workflow state

### IMAP client

Files:

- [`src/mail_ai_agent/imap_client.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/imap_client.py)

Responsibilities:

- connect to mailbox
- fetch candidates from source folder
- copy messages to target folder
- set `\\Flagged` when needed
- delete source after successful copy using `delete_message`

Operational safety rule:

- prefer `UID EXPUNGE` whenever the server supports `UIDPLUS`
- on the current production host, folder-level `EXPUNGE` is only allowed through explicit per-mailbox override and is guarded against unrelated `\\Deleted` messages

### State manager

Files:

- [`src/mail_ai_agent/state_manager.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/state_manager.py)

Responsibilities:

- mailbox-scoped workflow state in SQLite
- global worker lock
- per-message lease and retry control
- idempotency
- cleanup candidate tracking

The SQLite state includes:

- `mailbox_id`
- `message_id`
- `fingerprint`
- IMAP metadata
- workflow status
- lease info
- model/rule metadata

Important design choice:

- uniqueness is mailbox-scoped, not global

### Audit and reports

Files:

- [`src/mail_ai_agent/audit_logger.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/audit_logger.py)
- [`src/mail_ai_agent/reporting.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/reporting.py)
- [`src/mail_ai_agent/review_report.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/review_report.py)

Responsibilities:

- write JSONL audit entries
- summarize audit and state
- export CSV for manual review

Audit is mailbox-scoped and includes:

- `mailbox_id`
- `mailbox_user`
- message metadata
- status transitions
- action taken
- error text if any

### Drafts

Files:

- [`src/mail_ai_agent/draft_store.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/draft_store.py)

Responsibilities:

- save local draft suggestions for high-confidence reply-worthy messages

### Worker and CLI

Files:

- [`src/mail_ai_agent/main.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/main.py)
- [`src/mail_ai_agent/cli.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/cli.py)

Responsibilities:

- orchestrate whole flow
- process mailboxes sequentially
- expose JSON run report

### Metrics and monitoring

Files:

- [`src/mail_ai_agent/metrics_exporter.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/metrics_exporter.py)
- [`com.mailai.metrics.prod.plist`](/Users/gniewkob/Repos/priv/mAIl/com.mailai.metrics.prod.plist)
- [`scripts/prod_metrics.sh`](/Users/gniewkob/Repos/priv/mAIl/scripts/prod_metrics.sh)

Responsibilities:

- expose operational state and audit-derived quality metrics on `127.0.0.1:9177/metrics`
- provide Prometheus scrape input for Grafana
- separate current state from historical quality/distribution metrics

## Scheduling

Files:

- [`com.mailai.multi.test.plist`](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.test.plist)
- [`com.mailai.multi.prod.plist`](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.prod.plist)
- [`docs/launchd-setup.md`](/Users/gniewkob/Repos/priv/mAIl/docs/launchd-setup.md)

`launchd` is the scheduler. It runs the worker every 300 seconds.

Current operational intent:

- multi-prod `launchd` is the active scheduled worker
- multi-test `launchd` is now reserved for rollback or controlled validation only

## Runtime files

### Test mode

- env: [`.env.multi.test`](/Users/gniewkob/Repos/priv/mAIl/.env.multi.test)
- state: [`data/multi-test-state.sqlite`](/Users/gniewkob/Repos/priv/mAIl/data/multi-test-state.sqlite)
- audit: [`logs/multi-test-audit.jsonl`](/Users/gniewkob/Repos/priv/mAIl/logs/multi-test-audit.jsonl)
- drafts: `drafts/multi-test-pending`

### Production mode

- env: [`.env.multi.prod`](/Users/gniewkob/Repos/priv/mAIl/.env.multi.prod)
- state: `data/multi-prod-state.sqlite`
- audit: `logs/multi-prod-audit.jsonl`
- drafts: `drafts/multi-prod-pending`

## IMAP folder strategy

The worker now uses mailbox-specific production folders in the active manifest.

Historical test path:

- source: `INBOX.Test-AI-Review`
- target examples:
  - `INBOX.Test-Appointments`
  - `INBOX.Test-Questions`
  - `INBOX.Test-Complaints`
  - `INBOX.Test-Other`
  - `INBOX.Test-AI-Uncertain`

Current production path:

- source: `INBOX.AI-Review`
- target examples:
  - `INBOX.Appointments`
  - `INBOX.Questions`
  - `INBOX.Complaints`
  - `INBOX.Other`
  - `INBOX.Billing`
  - `INBOX.System`
  - `INBOX.AI-Uncertain`

## Multi-mailbox scope

Current active scope:

- 8 working mailboxes in [`config/mailboxes.active.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json)
- `larysa@bodora.pl` is intentionally excluded until password is available

Important rule:

- aliases are not configured as separate IMAP accounts
- only real IMAP logins appear in the active manifest

## Safety model

The project favors reversible operations.

Current safety controls:

- `DRY_RUN=false` on the active production worker
- global worker lock
- mailbox-scoped idempotency
- per-message lease with retries
- `copy -> delete_message` routing with `UID EXPUNGE` preferred
- explicit per-mailbox `imap_allow_folder_expunge` override in production because the current IMAP host does not advertise `UIDPLUS`
- manual cleanup CLI
- automatic cleanup pass for `cleanup_pending`
- startup folder preflight before mailbox processing
- LLM failure fallback to `uncertain`
- audit for every processing attempt

## Testing model

Files:

- unit tests under [`tests/unit`](/Users/gniewkob/Repos/priv/mAIl/tests/unit)
- integration tests under [`tests/integration`](/Users/gniewkob/Repos/priv/mAIl/tests/integration)

Current state:

- local suite passes
- real IMAP dry-runs were executed on multiple mailboxes
- short `DRY_RUN=false` validation on test folders passed
- production cutover completed on 2026-03-29
- real issues found in production-like samples have been fixed:
  - LLM `entities=[]` normalization
  - fingerprint collisions on transactional messages
  - payment reminder misrouting into customer categories

## Current rollout phase

The system is currently in stable early production with monitoring and operator runbooks in place.

Source of truth:

- [`docs/continuation-handoff.md`](/Users/gniewkob/Repos/priv/mAIl/docs/continuation-handoff.md)

Operational guides:

- [`docs/multi-mailbox-operations.md`](/Users/gniewkob/Repos/priv/mAIl/docs/multi-mailbox-operations.md)
- [`docs/multi-prod-cutover.md`](/Users/gniewkob/Repos/priv/mAIl/docs/multi-prod-cutover.md)
- [`docs/project-done-checklist.md`](/Users/gniewkob/Repos/priv/mAIl/docs/project-done-checklist.md)

## Next development phase

The next phase is quality iteration:

- derive `rule suggestions` from production audit patterns
- review them manually
- validate against the golden set
- expand deterministic coverage without auto-applying production changes
