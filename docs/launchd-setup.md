# launchd Setup

Use this only after the test `dry-run` is stable.

Current state on 2026-03-29:

- `com.mailai.multi.prod` is the active worker
- test worker should stay unloaded unless rollback is required

## Files

- single-mailbox template: [`com.mailai.plist.template`](/Users/gniewkob/Repos/priv/mAIl/com.mailai.plist.template)
- ready single-mailbox test plist for this machine: [`com.mailai.test.plist`](/Users/gniewkob/Repos/priv/mAIl/com.mailai.test.plist)
- multi test plist for this machine: [`com.mailai.multi.test.plist`](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.test.plist)
- multi prod plist for this machine: [`com.mailai.multi.prod.plist`](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.prod.plist)
- multi-mailbox template: [`com.mailai.multi.plist.template`](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.plist.template)

## Prepare

1. Copy the template to a real plist file.
2. Replace every `REPLACE_ME` with the actual macOS username.
3. Keep the env file pointed to `.env.test` until test folders are stable.
4. Confirm the paths exist:
   - project directory
   - virtualenv Python
   - `logs/`
   - env file

For multi-mailbox mode:

1. keep the worker on [`.env.multi.test`](/Users/gniewkob/Repos/priv/mAIl/.env.multi.test)
2. keep `DRY_RUN=true` until test folders are stable across mailboxes
3. confirm [`config/mailboxes.active.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json) contains only mailboxes with working passwords

## Load manually

```bash
cp com.mailai.plist.template ~/Library/LaunchAgents/com.mailai.plist
launchctl unload ~/Library/LaunchAgents/com.mailai.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.mailai.plist
launchctl start com.mailai
```

## Check status

```bash
launchctl list | rg com.mailai
tail -n 50 logs/launchd-stdout.log
tail -n 50 logs/launchd-stderr.log
```

For the prefilled test plist on this machine:

```bash
cp com.mailai.test.plist ~/Library/LaunchAgents/com.mailai.test.plist
launchctl unload ~/Library/LaunchAgents/com.mailai.test.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.mailai.test.plist
launchctl start com.mailai.test
```

For the prefilled multi-mailbox test plist on this machine:

```bash
cp com.mailai.multi.test.plist ~/Library/LaunchAgents/com.mailai.multi.test.plist
launchctl unload ~/Library/LaunchAgents/com.mailai.multi.test.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.mailai.multi.test.plist
launchctl start com.mailai.multi.test
```

For the prefilled multi-mailbox production plist on this machine:

```bash
cp com.mailai.multi.prod.plist ~/Library/LaunchAgents/com.mailai.multi.prod.plist
launchctl unload ~/Library/LaunchAgents/com.mailai.multi.prod.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.mailai.multi.prod.plist
launchctl start com.mailai.multi.prod
```

Do not load the production plist before:

1. the observation window in `DRY_RUN=true` is complete
2. the short `DRY_RUN=false` test on test folders is accepted
3. the remaining mailbox password gaps are closed or intentionally excluded

## Recommended rollout

1. `.env.test` with `DRY_RUN=true`
2. test folders only
3. manual CLI runs
4. `launchd` with `DRY_RUN=true`
5. review audit/state outputs
6. only then consider `DRY_RUN=false` on test folders

For multi-mailbox rollout, apply the same sequence per mailbox group, not all accounts at once.

## Current default

For this machine, the normal active job is now:

```bash
launchctl list | rg com.mailai.multi.prod
```

Use the multi-test plist only for rollback or controlled validation.

## Maintenance recommendation

Run maintenance periodically after repeated dry-runs:

```bash
.venv/bin/python -m mail_ai_agent.maintenance_cli \
  --audit-log logs/test-audit.jsonl \
  --state-db data/test-state.sqlite \
  --draft-dir drafts/test-pending \
  --rotate-audit-max-bytes 500000 \
  --prune-drafts-older-than-days 14 \
  --vacuum-db
```

Multi-mailbox equivalent:

```bash
.venv/bin/python -m mail_ai_agent.maintenance_cli \
  --audit-log logs/multi-test-audit.jsonl \
  --state-db data/multi-test-state.sqlite \
  --draft-dir drafts/multi-test-pending \
  --rotate-audit-max-bytes 500000 \
  --prune-drafts-older-than-days 14 \
  --vacuum-db
```
