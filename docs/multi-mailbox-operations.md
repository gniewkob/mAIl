# Multi-Mailbox Operations

## Scope

This runbook is for the multi-mailbox worker using:

- [`config/mailboxes.active.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json)
- [`.env.multi.prod`](/Users/gniewkob/Repos/priv/mAIl/.env.multi.prod)

Current mode on 2026-03-29:

- production worker is active
- test folders were cleaned after rollout validation
- archive copies of earlier prod-test artifacts are in `logs/archive/` and `data/archive/`
- local full suite is currently `96 passed, 2 skipped`
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

- top row should show `healthy`, `processed`, `failed`, `cleanup_pending`, and `uncertain`
- `failed`, `cleanup_pending`, and `uncertain` should normally stay at `0`
- `rule_share` is a tuning metric, not an outage signal
- if Grafana looks wrong, verify the raw metrics path first:

```bash
curl -sS http://127.0.0.1:9177/metrics | sed -n '1,80p'
curl -sS 'http://127.0.0.1:9090/api/v1/query?query=mailai_health_ok'
curl -sS http://127.0.0.1:9090/api/v1/targets
```

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
- transactional mail mostly ending as `other`, `billing`, or `spam_or_offer`
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
2. If the pattern repeats, improve `rule_engine` or prompt.
3. Keep them on test folders until the pattern is stable.

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

## Next development stage

The next planned phase is quality iteration driven by production evidence.

Current recommended order:

1. generate `rule suggestions` from repeated `route_source=llm` patterns
2. review candidates manually and promote only low-risk repetitive cases into deterministic rules
3. expand the golden set with anonymized real production examples
4. review quality weekly in Grafana and with `quality_report_cli`
5. keep runtime hardening stable unless new operational failures appear

## Adding the last mailbox later

When `larysa@bodora.pl` password is available:

1. update [`config/mailboxes.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.json)
2. regenerate [`config/mailboxes.active.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json)
3. create `INBOX.Test-*` on that mailbox
4. run one isolated dry-run before adding it to the scheduled worker
