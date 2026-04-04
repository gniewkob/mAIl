# Quality Review Checklist

## Goal

Use this checklist when reviewing real production routing quality for important mailboxes.

## Sample size

- minimum: 20 newest processed messages
- preferred: 50 messages across at least 3 mailboxes
- always include any `uncertain`, `failed`, or `cleanup_pending` if present

## Commands

```bash
.venv/bin/python -m mail_ai_agent.review_report_cli \
  --audit-log logs/multi-prod-audit.jsonl \
  --export-csv output/multi-prod-review.csv

.venv/bin/python -m mail_ai_agent.report_cli \
  --state-db data/multi-prod-state.sqlite \
  --audit-log logs/multi-prod-audit.jsonl

.venv/bin/python -m mail_ai_agent.quality_report_cli \
  --audit-log logs/multi-prod-audit.jsonl

.venv/bin/python -m mail_ai_agent.golden_set_cli \
  tests/synthetic_data/golden_batch_001.json
```

## Must be correct

- invoices, payment reminders, settlements: `INBOX.Billing`
- obvious bounces and mailer notifications: `INBOX.System`
- clear complaints and dissatisfaction: `INBOX.Complaints`
- clear customer questions requiring reply: `INBOX.Questions`
- obvious spam, cold outreach, newsletters, transactional shop noise: `INBOX.Other`

## Must not happen

- billing or payment mail in `INBOX.Questions`
- complaints in `INBOX.Other`
- customer question in `INBOX.System`
- marketing/cold outreach in customer folders
- real customer mail left in `INBOX.AI-Review`

## Review dimensions

For each reviewed message, check:

- source mailbox
- sender pattern
- subject pattern
- final folder
- whether a draft was created
- whether confidence looks realistic
- whether the decision should have been deterministic instead of LLM-based

## Escalate immediately

- any misrouted billing message
- any misrouted complaint
- repeated false `spam_or_offer`
- repeated `uncertain` for the same sender or subject family
- any `mailbox_failed`, `cleanup_pending`, or `Refusing folder-level expunge`

## Improvement loop

1. Add deterministic rules for stable, repeated patterns.
2. Keep important operational categories out of the LLM path where possible.
3. Re-run canary after each routing change:

```bash
bash scripts/prod_canary.sh
```
