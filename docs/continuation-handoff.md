# Continuation Handoff

## Current status

As of 2026-03-29:

- multi-mailbox implementation is complete
- local test suite passes: `96 passed, 2 skipped`
- payment routing fix is in place and covered by tests
- active production manifest exists: [`config/mailboxes.active.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json)
- multi-prod env is active: [`.env.multi.prod`](/Users/gniewkob/Repos/priv/mAIl/.env.multi.prod)
- `larysa@bodora.pl` is still excluded until the password is available

Important:

- the active scheduled worker is `com.mailai.multi.prod`
- the active metrics exporter is `com.mailai.metrics.prod`
- production folders were created on all active mailboxes
- test folders were cleaned after end-to-end validation
- test artifacts from the rollout are archived under `logs/archive/` and `data/archive/`
- Grafana dashboard `mAiL Overview` in folder `mAiL` is the preferred operator view
- the next development phase is rule-quality iteration, not more rollout work

## Exact next step

Do a production health check, not another rollout step.

Commands:

```bash
.venv/bin/python -m mail_ai_agent.status_cli \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl \
  --json

.venv/bin/python -m mail_ai_agent.report_cli \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl
```

Verify:

```bash
launchctl list | rg com.mailai.multi.prod
tail -n 50 /Users/gniewkob/Repos/priv/mAIl/logs/launchd-multi-prod-stdout.log
tail -n 50 /Users/gniewkob/Repos/priv/mAIl/logs/launchd-multi-prod-stderr.log
tail -n 50 /Users/gniewkob/Repos/priv/mAIl/logs/launchd-metrics-prod-stderr.log
```

## What already happened

- 48-hour `DRY_RUN=true` observation window completed cleanly
- short `DRY_RUN=false` validation completed on test folders
- final end-to-end routing check passed on:
  - billing -> `INBOX.Test-Billing`
  - question -> `INBOX.Test-Questions`
- production folders were then created and the manifest was switched from `INBOX.Test-*` to production folders
- production `launchd` worker was loaded successfully

## What the next agent should do

1. read this file first
2. confirm `com.mailai.multi.prod` is loaded
   legacy `com.salonbw.*` labels are retired and should not be used
3. inspect:
   - `logs/multi-prod-audit.jsonl`
   - `data/multi-prod-state.sqlite`
   - `logs/launchd-multi-prod-stderr.log`
   - `logs/launchd-multi-prod-stdout.log`
4. summarize:
   - new `failed`
   - repeated `uncertain`
   - any IMAP copy errors
   - obvious routing mistakes
   - whether Grafana and Prometheus still match the raw exporter metrics
5. decide whether:
   - continue production observation,
   - apply a targeted fix,
   - or roll back to test mode if a real IMAP mutation problem appears

## Production check cadence

- first check: about 1 hour after cutover
- second check: after the first working day
- later: daily or after any rule / prompt / folder mapping change

## Next development phase

Do not auto-tune rules in production.

Recommended next step:

1. collect repeated `route_source=llm` patterns from audit
2. propose deterministic `rule suggestions`
3. validate them against the golden set and unit tests
4. only then merge rule expansions

## If rollback is needed

```bash
launchctl unload ~/Library/LaunchAgents/com.mailai.multi.prod.plist 2>/dev/null || true
cp /Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.test.plist ~/Library/LaunchAgents/com.mailai.multi.test.plist
launchctl load ~/Library/LaunchAgents/com.mailai.multi.test.plist
launchctl start com.mailai.multi.test
```

Then inspect the latest prod audit and state before deciding the next action.
