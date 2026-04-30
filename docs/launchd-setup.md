# launchd Setup

## Current state

Normal active jobs on this machine:

- worker: `com.mailai.multi.prod`
- metrics exporter: `com.mailai.metrics.prod`
- quality learning report: `com.mailai.learning.prod`
- weekly autotune: `com.mailai.autotune.weekly.prod`

Test jobs should stay unloaded unless rollback or controlled validation is needed.

## Relevant files

- [com.mailai.multi.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.prod.plist)
- [com.mailai.multi.test.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.test.plist)
- [com.mailai.multi.plist.template](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.plist.template)
- [com.mailai.metrics.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.metrics.prod.plist)
- [com.mailai.learning.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.learning.prod.plist)
- [com.mailai.autotune.weekly.prod.plist](/Users/gniewkob/Repos/priv/mAIl/com.mailai.autotune.weekly.prod.plist)

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

## Verify status

```bash
launchctl list | rg 'com.mailai.multi.prod|com.mailai.metrics.prod'
tail -n 50 logs/launchd-multi-prod-stdout.log
tail -n 50 logs/launchd-multi-prod-stderr.log
tail -n 50 logs/launchd-metrics-prod-stderr.log
tail -n 50 logs/launchd-learning-prod-stdout.log
tail -n 50 logs/launchd-learning-prod-stderr.log
tail -n 50 logs/launchd-autotune-weekly-prod-stdout.log
tail -n 50 logs/launchd-autotune-weekly-prod-stderr.log
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
