# Multi-Mailbox Operations

## Scope

This runbook is for the multi-mailbox worker using:

- [`config/mailboxes.active.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json)
- [`.env.multi.prod`](/Users/gniewkob/Repos/priv/mAIl/.env.multi.prod)

Current mode:

- production worker is active
- Prometheus exporter is active on `127.0.0.1:9177`
- Grafana dashboard `mAiL Overview` is the preferred operator view

## Daily check

Run:

```bash
bash scripts/prod_healthcheck.sh

.venv/bin/python -m mail_ai_agent.report_cli \
  --audit-log logs/multi-prod-audit.jsonl \
  --state-db data/multi-prod-state.sqlite
```

Review:

- [`logs/multi-prod-audit.jsonl`](/Users/gniewkob/Repos/priv/mAIl/logs/multi-prod-audit.jsonl)
- [`data/multi-prod-state.sqlite`](/Users/gniewkob/Repos/priv/mAIl/data/multi-prod-state.sqlite)
- [`logs/launchd-multi-prod-stdout.log`](/Users/gniewkob/Repos/priv/mAIl/logs/launchd-multi-prod-stdout.log)
- [`logs/launchd-multi-prod-stderr.log`](/Users/gniewkob/Repos/priv/mAIl/logs/launchd-multi-prod-stderr.log)
- [`logs/launchd-metrics-prod-stdout.log`](/Users/gniewkob/Repos/priv/mAIl/logs/launchd-metrics-prod-stdout.log)
- [`logs/launchd-metrics-prod-stderr.log`](/Users/gniewkob/Repos/priv/mAIl/logs/launchd-metrics-prod-stderr.log)

Optional alert push:

```bash
ALERT_WEBHOOK_URL=https://example.invalid/webhook \
  bash scripts/prod_alert.sh
```

Metrics preview:

```bash
.venv/bin/python -m mail_ai_agent.metrics_exporter \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl \
  --stdout-log logs/launchd-multi-prod-stdout.log \
  --stderr-log logs/launchd-multi-prod-stderr.log \
  --oneshot
```

Grafana quick checks:

- top row should show `Operational Health`, `Success Ratio (Current Totals)`, `Processed`, `Failed`, `Cleanup Pending`, and `Uncertain`
- `failed` and `cleanup_pending` should normally stay at `0`
- `uncertain` should stay low and concentrated only in genuinely ambiguous mail; repeated growth on one mailbox means policy or prompt tuning is needed
- `Category Breakdown` should distinguish `spam`, `newsletter`, `offer`, `other`, and `parse_error`
- `Parse Errors (Total)` should normally stay at `0`
- `Current Uncertain by Mailbox` should identify the mailbox that needs quality tuning
- `Current Failed by Mailbox` may show `No data` when current failed state is `0`
- `Quality Suggestions` shows how many current proposals exist in each class: `rule_engine`, `prompt`, `parser`, `ops`
- if Grafana looks wrong, verify the raw metrics path first:

```bash
curl -sS http://127.0.0.1:9177/metrics | sed -n '1,80p'
curl -sS 'http://127.0.0.1:9090/api/v1/query?query=mailai_health_ok'
curl -sS http://127.0.0.1:9090/api/v1/targets
```

## Dashboard v2 (recommended)

Import dashboard JSON:

- [grafana-mailai-overview-v2.json](/Users/gniewkob/Repos/priv/mAIl/dashboards/grafana-mailai-overview-v2.json)

Panels to watch first:

- `Operational Health`: single SRE status for fast triage
- `Current Uncertain / Failed / Cleanup Pending`: immediate backlog
- `State Timeline`: health trend
- `Event Outcomes`: rule vs LLM routing and failures
- `Category Distribution`: drift signal (`other`, `offer`, `newsletter`, `spam`)
- `Mailbox Hotspots (Current)`: where to fix first
- `Action Breakdown (Instant)`: operational mix
- `Autotune Proposals (Instant)`: weekly improvement priorities

Daily operator flow:

1. Check `Operational Health`.
2. If backlog tiles are non-zero, inspect `Mailbox Hotspots (Current)`.
3. Check `Event Outcomes` and `Category Distribution` for regressions.
4. Use `Autotune Proposals` and weekly autotune output before manual rule edits.

## One-hour production check

Run once after cutover and after any routing change:

```bash
bash scripts/prod_canary.sh

.venv/bin/python -m mail_ai_agent.status_cli \
  --audit-log logs/multi-prod-audit.jsonl \
  --state-db data/multi-prod-state.sqlite \
  --json

.venv/bin/python -m mail_ai_agent.report_cli \
  --audit-log logs/multi-prod-audit.jsonl \
  --state-db data/multi-prod-state.sqlite

tail -n 40 logs/launchd-multi-prod-stderr.log
tail -n 40 logs/launchd-metrics-prod-stderr.log
tail -n 20 logs/multi-prod-audit.jsonl
```

## What is normal

- `skip_already_done` on repeated scans of already-processed messages
- only some mailboxes having candidates on a given run
- transactional mail mostly ending as `other`, `billing`, `newsletter`, or `offer`
- zero candidates in the first hour after cutover

## What requires action

- any new `failed`
- any `Refusing folder-level expunge ...` error
- any IMAP `Unable to copy message ...` error
- repeated `uncertain` for the same mailbox pattern
- wrong routing of complaints
- wrong routing of cold outreach into customer folders
- wrong routing of billing or payment reminders into customer folders

## Known quality fix applied on 2026-03-27

Payment-related messages were observed being misrouted by the model on `kontakt@gliwicka111.pl`.

Examples:

- `Przypomnienie o terminie płatności`
- `Brak płatności w terminie`

Response:

- deterministic billing/payment matching was expanded in [`src/mail_ai_agent/rule_engine.py`](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/rule_engine.py)
- these cases should now route as `billing` without LLM

When reviewing the next observation window, verify this fix first.

## Failed messages

1. Find the mailbox in audit by `mailbox_id`.
2. Read the error and message metadata.
3. Fix parser, rule, or LLM normalization.
4. Re-run the worker. Failed rows can be reacquired until retry policy is exhausted.

## Uncertain messages

1. Review them manually in audit or CSV.
2. If the pattern is mostly low-signal `other`, lower-risk automation is preferred over human backlog.
3. The current policy already routes `other` with moderate confidence directly to `INBOX.Other`; keep `uncertain` for low-confidence or operational failures.
4. If the pattern repeats, improve `rule_engine` or prompt.

Safe replay without deleting originals:

```bash
.venv/bin/python -m mail_ai_agent.historical_backfill_cli \
  --env-file .env.multi.prod \
  --apply \
  --keep-source \
  --force-reprocess \
  --folders INBOX.AI-Uncertain
```

## Quality-learning patch workflow

Generated `rule_engine` proposals stay read-only until explicitly accepted.

1. Generate the latest report and proposal patch:

```bash
.venv/bin/python -m mail_ai_agent.quality_learning_cli \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl \
  --output-dir logs/quality-learning
```

2. Validate the patch without changing code:

```bash
.venv/bin/python -m mail_ai_agent.apply_quality_patch_cli \
  --patch logs/quality-learning/quality-learning-YYYYMMDDTHHMMSSZ-proposal-1.patch \
  --check
```

3. Apply only after review:

```bash
.venv/bin/python -m mail_ai_agent.apply_quality_patch_cli \
  --patch logs/quality-learning/quality-learning-YYYYMMDDTHHMMSSZ-proposal-1.patch \
  --apply
```

Operational guarantees:

- `--check` runs a dry-run patch validation only
- `--apply` creates a timestamped backup of the target file
- `--apply` runs focused tests for `rule_engine` and `quality_learning`
- failed validation restores the backup automatically
- the result is logged to `logs/quality-learning/quality-patch-*.json`

## Cleanup

Dry plan:

```bash
.venv/bin/python -m mail_ai_agent.cleanup_cli \
  --env-file .env.multi.prod \
  --mailbox-id kontakt_salon_bw
```

Apply only after verification:

```bash
.venv/bin/python -m mail_ai_agent.cleanup_cli \
  --env-file .env.multi.prod \
  --mailbox-id kontakt_salon_bw \
  --apply
```

For source-folder cleanup:

```bash
.venv/bin/python -m mail_ai_agent.cleanup_cli \
  --env-file .env.multi.prod \
  --mailbox-id kontakt_salon_bw \
  --apply
```

## Ongoing improvement loop

Use production evidence to drive small, reversible quality changes.

Current recommended order:

1. generate `rule suggestions` from repeated `route_source=llm` patterns
2. review candidates manually and promote only low-risk repetitive cases into deterministic rules
3. expand the golden set with anonymized real production examples
4. review quality weekly in Grafana and with `quality_report_cli`
5. keep runtime hardening stable unless new operational failures appear

## Adding the last mailbox later

When credentials for `larysa@bodora.pl` are available:

1. update [`config/mailboxes.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.json)
2. regenerate [`config/mailboxes.active.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json)
3. create `INBOX.Test-*` on that mailbox
4. run one isolated dry-run before adding it to the scheduled worker
