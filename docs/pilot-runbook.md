# Pilot Runbook

## Goal

Use the worker on a real mailbox in a controlled way, with enough observability to stop before any bad automation compounds.

Use this only for onboarding a new mailbox or validating a major routing change in isolation. It is not the primary runbook for the current production estate.

## Preconditions

- Source folder is dedicated to this workflow, ideally `INBOX.AI-Review`.
- No second worker or human cleanup process operates on the same source folder.
- Operators understand that folder-level `EXPUNGE` is used during cleanup.
- `.venv` exists and unit tests pass.

## Recommended pilot config

- `DRY_RUN=true` for the first observation phase.
- `IMAP_SEARCH_CRITERION=UNSEEN`
- conservative `IMAP_FETCH_LIMIT`, for example `25` or `50`
- normal IMAP retry settings enabled

## Dry-run phase

1. Run:

```bash
.venv/bin/python -m pytest -q
```

2. Execute the worker in simulation mode:

```bash
.venv/bin/python -m mail_ai_agent.cli --env-file .env.test --json
```

3. Check compact runtime status:

```bash
.venv/bin/python -m mail_ai_agent.status_cli --state-db data/test-state.sqlite --audit-log logs/test-audit.jsonl --json
```

4. Verify:

- no IMAP moves happened
- no draft files were created
- audit contains `simulated` outcomes only
- classification quality is acceptable

## Controlled live phase

1. Flip `DRY_RUN=false`.
2. Keep using the dedicated source folder.
3. Run one worker only.
4. Review status after each run:

```bash
.venv/bin/python -m mail_ai_agent.status_cli --state-db data/state.sqlite --audit-log logs/audit.jsonl
```

## Stop conditions

Pause rollout immediately if any of the following appears:

- unexpected `failed` growth
- persistent `cleanup_pending`
- any `cleanup_uidvalidity_mismatch`
- folder behavior inconsistent with audit log

## Recovery checks

- `cleanup_pending` should normally trend back to zero on the next successful pass.
- `cleanup_uidvalidity_mismatch` should be investigated manually before retrying cleanup.
- if `failed` rises, inspect `logs/audit.jsonl` before changing rules or config.
