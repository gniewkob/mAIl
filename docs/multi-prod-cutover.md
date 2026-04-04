# Multi-Prod Cutover

Status on 2026-03-29:

- completed
- production folders were created on all 8 active mailboxes
- manifest was switched from `INBOX.Test-*` to production folders
- `com.mailai.multi.prod` is the active scheduled worker

## Preconditions

- [`docs/project-done-checklist.md`](/Users/gniewkob/Repos/priv/mAIl/docs/project-done-checklist.md) is satisfied
- [`docs/multi-mailbox-operations.md`](/Users/gniewkob/Repos/priv/mAIl/docs/multi-mailbox-operations.md) is understood
- `DRY_RUN=false` was verified on test folders
- active mailbox manifest contains only approved mailboxes

## Files

- env: [`.env.multi.prod`](/Users/gniewkob/Repos/priv/mAIl/.env.multi.prod)
- plist for this machine: [`com.mailai.multi.prod.plist`](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.prod.plist)

## Cutover sequence

1. Verify active mailbox manifest.
2. Confirm production source folders per mailbox are correct.
3. Back up current logs and state if needed.
4. Run one manual production-mode invocation:

```bash
.venv/bin/python -m mail_ai_agent.cli --env-file .env.multi.prod --json
```

5. Review:

- [`logs/multi-prod-audit.jsonl`](/Users/gniewkob/Repos/priv/mAIl/logs/multi-prod-audit.jsonl)
- [`data/multi-prod-state.sqlite`](/Users/gniewkob/Repos/priv/mAIl/data/multi-prod-state.sqlite)

6. Only then load the production `launchd` plist.

## Rollback

1. unload production plist
2. switch back to test worker only
3. inspect audit and state
4. use controlled cleanup manually, never blindly

## First post-cutover check

Run after roughly 1 hour:

```bash
.venv/bin/python -m mail_ai_agent.status_cli \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl \
  --json

.venv/bin/python -m mail_ai_agent.report_cli \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl

tail -n 80 logs/launchd-multi-prod-stderr.log
tail -n 120 logs/launchd-multi-prod-stdout.log
tail -n 20 logs/multi-prod-audit.jsonl
```

Expected:

- no new `failed`
- no `cleanup_pending`
- no IMAP `copy` errors
- some real traffic is acceptable, zero traffic is also acceptable
