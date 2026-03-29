# AI Mail Triage MVP

Local email triage worker built around:

- IMAP
- Python
- Ollama for local classification
- SQLite for workflow state and leases
- JSONL audit logging

## What is included in the public repo

This repository is prepared for public sharing:

- real `.env` files are ignored
- real mailbox manifests are ignored
- runtime data, logs, drafts, and generated output are ignored
- only example configuration files are kept in git

## Setup

1. Create a virtual environment:

```bash
python3 -m venv .venv
```

2. Install dependencies:

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
```

3. Create `.env` from `.env.example` and fill in real values.
4. For safer IMAP testing, create `.env.test` from `.env.test.example`.
5. For multi-mailbox mode, copy `config/mailboxes.example.json` to a local manifest such as `config/mailboxes.local.json` and point `MAILBOXES_CONFIG_PATH` at that file.
   Prefer `imap_pass_ref` over plaintext `imap_pass`. Supported refs:
   `env:VAR_NAME`, `keychain:service/account`, `keychain:service:account`.

## Single-mailbox mode

Required settings:

- `IMAP_HOST`
- `IMAP_USER`
- `IMAP_PASS`

Run:

```bash
.venv/bin/python -m mail_ai_agent.cli --json
```

Use a custom env file:

```bash
.venv/bin/python -m mail_ai_agent.cli --env-file .env.test --json
```

## Multi-mailbox mode

Set `MAILBOXES_CONFIG_PATH` in your env file to your local mailbox manifest.

Start from the example:

- [config/mailboxes.example.json](config/mailboxes.example.json)

Prepare local files:

```bash
cp .env.multi.test.example .env.multi.test
cp config/mailboxes.example.json config/mailboxes.local.json
```

If you are starting from a manifest with plaintext `imap_pass`, migrate it first:

```bash
.venv/bin/python -m mail_ai_agent.manifest_secrets_cli \
  --input config/mailboxes.local.json \
  --output config/mailboxes.local.refs.json \
  --mode env \
  --sidecar-output output/mailboxes.local.secrets.sh
```

Then update `.env.multi.test` so `MAILBOXES_CONFIG_PATH=config/mailboxes.local.json`, configure secret refs for each mailbox, and run:

```bash
.venv/bin/python -m mail_ai_agent.cli --env-file .env.multi.test --json
```

Preflight the mailbox topology before enabling the worker:

```bash
.venv/bin/python -m mail_ai_agent.preflight_cli --env-file .env.multi.test
```

Production multi-mailbox mode uses the same manifest structure, but with mailbox-specific production folders such as `INBOX.AI-Review`, `INBOX.Other`, and `INBOX.Billing`.

## Runtime behavior

- processed mail is moved with IMAP `copy -> delete_message`
- deterministic complaint rules add `\\Flagged`
- if copy succeeds but source cleanup fails, state is stored as `cleanup_pending` with action `move_copy_succeeded_cleanup_pending`
- worker runs an automatic cleanup pass for `cleanup_pending` records before processing new candidates
- `cleanup_cli` can still retry source-folder cleanup manually for pending records
- startup preflight validates source and target folders before mailbox processing begins
- cleanup pass verifies stored `UIDVALIDITY` before deleting from source; mismatches are skipped and logged
- IMAP operations use retry and reconnect with `IMAP_MAX_RETRIES` and `IMAP_RETRY_BACKOFF_SECONDS`
- source deletion prefers IMAP `UID EXPUNGE`; folder-level `EXPUNGE` is disabled by default and requires explicit `IMAP_ALLOW_FOLDER_EXPUNGE=true`
- when folder-level `EXPUNGE` is used, the worker now refuses to proceed if the source folder already contains other `\\Deleted` messages and aborts if the deleted set is not exactly the current message
- candidate selection is configurable with `IMAP_SEARCH_CRITERION` and capped by `IMAP_FETCH_LIMIT`
- `IMAP_SEARCH_CRITERION` is intentionally limited to a small safe whitelist: `ALL`, `UNSEEN`, `UNANSWERED`, `FLAGGED`, `UNSEEN UNANSWERED`, `UNSEEN FLAGGED`
- fetched candidates now carry IMAP `UIDVALIDITY` into persisted workflow state
- state stores both a message fingerprint and a content fingerprint; content fingerprint is used only as fallback deduplication when `Message-ID` is missing
- state stores `target_uid` when the IMAP server returns `COPYUID`, which improves post-copy recovery visibility
- `DRY_RUN=true` is a simulation mode: no IMAP mutation, no terminal SQLite state, no draft files
- cleanup candidate selection now targets only explicit `cleanup_pending` records; legacy cleanup heuristics are no longer part of the main runtime path
- `LLM_FAILURE_ROUTE_TO_UNCERTAIN=true` routes LLM outages or invalid model output to `INBOX.AI-Uncertain` state instead of silently exhausting retries
- audit logs redact direct PII fields by default; set `AUDIT_REDACT_PII=false` only for tightly controlled debugging

## Operational assumptions

- `INBOX.AI-Review` should be treated as a worker-owned source folder in production.
- Prefer IMAP servers with `UIDPLUS`; use `IMAP_ALLOW_FOLDER_EXPUNGE=true` only when the source folder is exclusively owned by this worker.
- The current production host on `mail0.mydevil.net` does not advertise `UIDPLUS`; the active multi-mailbox manifest therefore sets `imap_allow_folder_expunge: true` per mailbox as an explicit operational override after preflight verification.
- Do not run multiple workers or manual IMAP cleanup flows against the same source folder at the same time.
- `IMAP_SEARCH_CRITERION=UNSEEN` is the recommended pilot setting. `ALL` is supported, but paired with a low `IMAP_FETCH_LIMIT` it can hide backlog behavior.

## Operational commands

Compact status:

```bash
.venv/bin/python -m mail_ai_agent.status_cli --state-db data/state.sqlite --audit-log logs/audit.jsonl
```

Full structured report:

```bash
.venv/bin/python -m mail_ai_agent.report_cli --state-db data/state.sqlite --audit-log logs/audit.jsonl
```

Multi-mailbox production status:

```bash
.venv/bin/python -m mail_ai_agent.status_cli --state-db data/multi-prod-state.sqlite --audit-log logs/multi-prod-audit.jsonl --json
```

Multi-mailbox production report:

```bash
.venv/bin/python -m mail_ai_agent.report_cli --state-db data/multi-prod-state.sqlite --audit-log logs/multi-prod-audit.jsonl
```

Production healthcheck:

```bash
.venv/bin/python -m mail_ai_agent.healthcheck_cli \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl \
  --stdout-log logs/launchd-multi-prod-stdout.log \
  --stderr-log logs/launchd-multi-prod-stderr.log
```

Manual cleanup preview:

```bash
.venv/bin/python -m mail_ai_agent.cleanup_cli
```

Apply cleanup:

```bash
.venv/bin/python -m mail_ai_agent.cleanup_cli --apply
```

PII scrub for existing state and drafts:

```bash
.venv/bin/python -m mail_ai_agent.maintenance_cli \
  --state-db data/multi-prod-state.sqlite \
  --scrub-state-pii

.venv/bin/python -m mail_ai_agent.maintenance_cli \
  --draft-dir drafts/multi-prod-pending \
  --scrub-draft-pii
```

Grafana / Prometheus checks:

```bash
bash scripts/prod_metrics.sh
curl -sS http://127.0.0.1:9177/metrics | sed -n '1,80p'
curl -sS 'http://127.0.0.1:9090/api/v1/query?query=mailai_health_ok'
```

The production dashboard is `mAiL Overview` in the Grafana folder `mAiL`.

- top row: current operational state
- mailbox/category/route charts: historical distribution from audit-derived metrics
- `rule_share`: quality tuning signal, not an outage signal
- `failed`, `cleanup_pending`, and `uncertain`: operational risk signals

## Bootstrap

```bash
bash scripts/bootstrap.sh
```

Production helpers:

```bash
bash scripts/prod_healthcheck.sh
bash scripts/prod_canary.sh
bash scripts/prod_alert.sh
bash scripts/prod_metrics.sh
```

Quality helpers:

```bash
.venv/bin/python -m mail_ai_agent.quality_report_cli \
  --audit-log logs/multi-prod-audit.jsonl

.venv/bin/python -m mail_ai_agent.golden_set_cli \
  tests/synthetic_data/golden_batch_001.json
```

Metrics exporter:

```bash
.venv/bin/python -m mail_ai_agent.metrics_exporter \
  --host 127.0.0.1 \
  --port 9177 \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl \
  --stdout-log logs/launchd-multi-prod-stdout.log \
  --stderr-log logs/launchd-multi-prod-stderr.log
```

One-shot Prometheus output preview:

```bash
.venv/bin/python -m mail_ai_agent.metrics_exporter \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl \
  --stdout-log logs/launchd-multi-prod-stdout.log \
  --stderr-log logs/launchd-multi-prod-stderr.log \
  --oneshot
```

For a persistent local exporter, load [com.mailai.metrics.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.metrics.prod.plist) and scrape `127.0.0.1:9177/metrics` from Prometheus.

## Next stage

The current hardening pass is complete. The next recommended development stage is quality iteration, not more core runtime changes.

Priority order:

1. build a `rule suggestions` workflow from production audit patterns
2. review repeated `route_source=llm` decisions and promote low-risk recurring cases into deterministic rules
3. expand the golden set with anonymized real examples from production
4. run regular quality review on `billing`, `question`, `complaint`, `other`, and `spam_or_offer`
5. keep Grafana as the operator view, but gate rule changes through review and tests

The system may suggest new rules periodically, but should not auto-apply them to production without review.

## Tests

Unit tests:

```bash
.venv/bin/pytest -q
```

Live Ollama test:

```bash
RUN_LIVE_OLLAMA_TESTS=1 .venv/bin/pytest tests/integration/test_ollama_live.py -q
```

Live IMAP test:

```bash
RUN_LIVE_IMAP_TESTS=1 \
LIVE_IMAP_HOST=mail.example.com \
LIVE_IMAP_USER=user@example.com \
LIVE_IMAP_PASS=change-me \
LIVE_IMAP_SOURCE_FOLDER=INBOX.Test-AI-Review \
.venv/bin/pytest tests/integration/test_imap_live.py -q
```

Do not point live IMAP tests at a production source folder.
