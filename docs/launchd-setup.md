# launchd Setup

## Current state

Normal active jobs on this machine:

- worker: `com.mailai.multi.prod`
- metrics exporter: `com.mailai.metrics.prod`
- quality learning report: `com.mailai.learning.prod`
- weekly autotune: `com.mailai.autotune.weekly.prod`
- maintenance: `com.mailai.maintenance.prod`
- inbox repair sync: `com.mailai.inbox.sync.repair.prod`
- inbox prune sync: `com.mailai.inbox.sync.prune.prod`
- uncertain autoclose: `com.mailai.uncertain.autoclose.prod`

Test jobs should stay unloaded unless rollback or controlled validation is needed.

## Relevant files

- [com.mailai.multi.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.prod.plist)
- [com.mailai.multi.test.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.test.plist)
- [com.mailai.multi.plist.template](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.plist.template)
- [com.mailai.metrics.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.metrics.prod.plist)
- [com.mailai.learning.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.learning.prod.plist)
- [com.mailai.autotune.weekly.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.autotune.weekly.prod.plist)
- [com.mailai.maintenance.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.maintenance.prod.plist)
- [com.mailai.inbox.sync.repair.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.inbox.sync.repair.prod.plist)
- [com.mailai.inbox.sync.prune.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.inbox.sync.prune.prod.plist)
- [com.mailai.uncertain.autoclose.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.uncertain.autoclose.prod.plist)

## Preconditions

Before loading production worker:

1. [preflight_cli.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/preflight_cli.py) passes for the active env.
2. Target folders exist, including `Junk`, `INBOX.Newsletter`, and `INBOX.Offer`.
3. `.env.multi.prod` points to the correct state DB and audit log.
4. Active mailbox manifest contains only approved mailboxes.

## Load production worker

```bash
cp com.mailai.multi.prod.plist ~/Library/LaunchAgents/com.mailai.multi.prod.plist
launchctl unload ~/Library/LaunchAgents/com.mailai.multi.prod.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.mailai.multi.prod.plist
launchctl start com.mailai.multi.prod
```

## Load inbox sync jobs

```bash
cp com.mailai.inbox.sync.repair.prod.plist ~/Library/LaunchAgents/com.mailai.inbox.sync.repair.prod.plist
cp com.mailai.inbox.sync.prune.prod.plist ~/Library/LaunchAgents/com.mailai.inbox.sync.prune.prod.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mailai.inbox.sync.repair.prod.plist 2>/dev/null || true
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mailai.inbox.sync.prune.prod.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mailai.inbox.sync.repair.prod.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mailai.inbox.sync.prune.prod.plist
```

## Load maintenance job

Runs daily at 03:30. It rotates the production audit log after 100 MB, keeps 10 rotated backups, prunes local pending drafts older than 30 days, and runs SQLite maintenance.

```bash
cp com.mailai.maintenance.prod.plist ~/Library/LaunchAgents/com.mailai.maintenance.prod.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mailai.maintenance.prod.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mailai.maintenance.prod.plist
```

## Restart jobs

Worker:

```bash
launchctl kickstart -k gui/$(id -u)/com.mailai.multi.prod
```

Metrics exporter:

```bash
launchctl kickstart -k gui/$(id -u)/com.mailai.metrics.prod
```

Quality learning:

```bash
launchctl kickstart -k gui/$(id -u)/com.mailai.learning.prod
```

Weekly autotune:

```bash
launchctl kickstart -k gui/$(id -u)/com.mailai.autotune.weekly.prod
```

Maintenance:

```bash
launchctl kickstart -k gui/$(id -u)/com.mailai.maintenance.prod
```

Inbox repair sync:

```bash
launchctl kickstart -k gui/$(id -u)/com.mailai.inbox.sync.repair.prod
```

Inbox prune sync:

```bash
launchctl kickstart -k gui/$(id -u)/com.mailai.inbox.sync.prune.prod
```

Uncertain autoclose:

```bash
launchctl kickstart -k gui/$(id -u)/com.mailai.uncertain.autoclose.prod
```

Current weekly learner hardening (`com.mailai.autotune.weekly.prod`):
- `--learner-mode autopromote`
- `--challenger-min-impact-score 0.15`
- `--challenger-max-policy-delta 15`

HTTP trigger UI (used by Grafana dashboard link):

```bash
open "http://127.0.0.1:9177/autotune/trigger-ui"
```

Protected trigger (manual/API use):

```bash
TOKEN="$(cat /Users/gniewkob/Repos/priv/mAIl/secrets/autotune_trigger.token)"
curl -sS -X POST -H "X-Trigger-Token: ${TOKEN}" "http://127.0.0.1:9177/autotune/trigger"
```

Manual trigger (reload + run now):

```bash
cp /Users/gniewkob/Repos/priv/mAIl/com.mailai.autotune.weekly.prod.plist \
  ~/Library/LaunchAgents/com.mailai.autotune.weekly.prod.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mailai.autotune.weekly.prod.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mailai.autotune.weekly.prod.plist
launchctl kickstart -k gui/$(id -u)/com.mailai.autotune.weekly.prod
```

Job definition:
- [com.mailai.autotune.weekly.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.autotune.weekly.prod.plist)

## Verify status

```bash
launchctl list | rg 'com.mailai.multi.prod|com.mailai.metrics.prod'
launchctl list | rg 'com.mailai.learning.prod|com.mailai.autotune.weekly.prod|com.mailai.maintenance.prod'
launchctl list | rg 'com.mailai.inbox.sync.repair.prod|com.mailai.inbox.sync.prune.prod'
launchctl list | rg 'com.mailai.uncertain.autoclose.prod'
tail -n 50 logs/launchd-multi-prod-stdout.log
tail -n 50 logs/launchd-multi-prod-stderr.log
tail -n 50 logs/launchd-metrics-prod-stderr.log
tail -n 50 logs/launchd-learning-prod-stdout.log
tail -n 50 logs/launchd-learning-prod-stderr.log
tail -n 50 logs/launchd-autotune-weekly-prod-stdout.log
tail -n 50 logs/launchd-autotune-weekly-prod-stderr.log
tail -n 50 logs/launchd-maintenance-prod-stdout.log
tail -n 50 logs/launchd-maintenance-prod-stderr.log
tail -n 50 logs/launchd-inbox-sync-repair-prod-stdout.log
tail -n 50 logs/launchd-inbox-sync-repair-prod-stderr.log
tail -n 50 logs/launchd-inbox-sync-prune-prod-stdout.log
tail -n 50 logs/launchd-inbox-sync-prune-prod-stderr.log
tail -n 50 logs/launchd-uncertain-autoclose-prod-stdout.log
tail -n 50 logs/launchd-uncertain-autoclose-prod-stderr.log
```

## Rollback to test worker

```bash
launchctl unload ~/Library/LaunchAgents/com.mailai.multi.prod.plist 2>/dev/null || true
cp com.mailai.multi.test.plist ~/Library/LaunchAgents/com.mailai.multi.test.plist
launchctl load ~/Library/LaunchAgents/com.mailai.multi.test.plist
launchctl start com.mailai.multi.test
```

## Maintenance

Example production maintenance:

```bash
.venv/bin/python -m mail_ai_agent.maintenance_cli \
  --audit-log logs/multi-prod-audit.jsonl \
  --state-db data/multi-prod-state.sqlite \
  --draft-dir drafts/multi-prod-pending \
  --rotate-audit-max-bytes 500000 \
  --prune-drafts-older-than-days 14 \
  --vacuum-db
```

Rollback scheduled maintenance:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mailai.maintenance.prod.plist 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.mailai.maintenance.prod.plist
```
