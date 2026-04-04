# Recovery Runbook

## Scope

This runbook covers the active production worker:

- [`config/mailboxes.active.json`](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json)
- [`.env.multi.prod`](/Users/gniewkob/Repos/priv/mAIl/.env.multi.prod)
- [`com.mailai.multi.prod.plist`](/Users/gniewkob/Repos/priv/mAIl/com.mailai.multi.prod.plist)

## First response

Run:

```bash
bash scripts/prod_healthcheck.sh

.venv/bin/python -m mail_ai_agent.status_cli \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl \
  --json

tail -n 40 logs/launchd-multi-prod-stderr.log
tail -n 40 logs/multi-prod-audit.jsonl
```

## `mailbox_failed`

1. Identify the mailbox in [`logs/multi-prod-audit.jsonl`](/Users/gniewkob/Repos/priv/mAIl/logs/multi-prod-audit.jsonl).
2. Run isolated preflight:

```bash
.venv/bin/python -m mail_ai_agent.preflight_cli \
  --env-file .env.multi.prod \
  --mailbox-id <mailbox_id>
```

3. If preflight fails, do not restart blindly. Fix credentials, folders, or IMAP capabilities first.
4. After fixing the root cause, restart the worker:

```bash
launchctl kickstart -k gui/$(id -u)/com.mailai.multi.prod
```

## `cleanup_pending`

1. Confirm current count in state.
2. Review the newest audit entry for the same `message_id_sha256` or `fingerprint`.
3. Retry cleanup for the affected mailbox only:

```bash
.venv/bin/python -m mail_ai_agent.cleanup_cli \
  --env-file .env.multi.prod \
  --mailbox-id <mailbox_id> \
  --apply
```

4. If cleanup still fails, stop and inspect the source folder manually before retrying again.

## `cleanup_uidvalidity_mismatch`

This means the source folder changed identity from the worker point of view.

1. Do not force cleanup.
2. Inspect the mailbox manually.
3. Treat the message as a manual recovery case.
4. Leave the state row untouched unless you are sure the source message is the same object.

## `Refusing folder-level expunge`

This is a deliberate safety stop.

Meaning:

- the source folder already contains other `\Deleted` messages, or
- the deleted set changed after marking the current UID.

Action:

1. Stop manual IMAP activity against `INBOX.AI-Review`.
2. Inspect the mailbox for concurrent clients or manual cleanup.
3. Clear the conflicting `\Deleted` state manually in the mail client if needed.
4. Restart the worker only after the source folder is clean again.

## High `uncertain`

1. Export a review slice:

```bash
.venv/bin/python -m mail_ai_agent.review_report_cli \
  --audit-log logs/multi-prod-audit.jsonl \
  --export-csv output/multi-prod-review.csv
```

2. Review recurring patterns.
3. Extend deterministic rules before changing the model prompt.

## PII scrub

Run after any incident that produced unexpected plaintext artifacts:

```bash
.venv/bin/python -m mail_ai_agent.maintenance_cli \
  --state-db data/multi-prod-state.sqlite \
  --scrub-state-pii

.venv/bin/python -m mail_ai_agent.maintenance_cli \
  --draft-dir drafts/multi-prod-pending \
  --scrub-draft-pii
```
