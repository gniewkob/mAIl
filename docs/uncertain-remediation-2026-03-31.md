# Uncertain Remediation and Malformed Mail Cleanup (2026-03-31)

This note records the production remediation completed on 2026-03-31 after historical mailbox backfill.

## Problem

- Historical backlog staging increased worker throughput, but several messages landed in `INBOX.AI-Uncertain`.
- One malformed message in `kontakt_salon_bw/INBOX` could not be parsed during historical backfill:
  - mailbox: `kontakt_salon_bw`
  - folder: `INBOX`
  - uid: `35642`
  - error: `unknown encoding: binary`
- Reprocessing confirmed that the remaining `uncertain` messages were not caused by mailbox routing anymore. They were specific LLM/runtime edge cases for historical messages.

## Actions Taken

### 1. Added admin mailbox operations

Implemented a dedicated admin CLI in [src/mail_ai_agent/admin_mailbox_cli.py](../src/mail_ai_agent/admin_mailbox_cli.py):

- `requeue-uncertain`
  - moves a message from `INBOX.AI-Uncertain` back to `INBOX.AI-Review`
  - deletes the corresponding `uncertain` row from SQLite state
  - writes an audit record with `action_taken=admin_requeue_uncertain`
- `delete-imap-message`
  - deletes one exact IMAP UID from a chosen mailbox and folder
  - writes an audit record with `action_taken=admin_delete_message`

Supporting state helpers were added in [src/mail_ai_agent/state_manager.py](../src/mail_ai_agent/state_manager.py):

- `list_by_status()`
- `delete_record()`

### 2. Removed the malformed production message

Deleted the single malformed message:

- mailbox: `kontakt_salon_bw`
- folder: `INBOX`
- uid: `35642`

Result:

- message removed successfully
- no additional mailbox cleanup issues were observed

### 3. Requeued historical uncertain messages

Requeued the original historical `uncertain` records:

- first pass: record ids `175,177,181,189`
- second pass: record ids `300,303,304`

Result:

- all affected messages were moved back to `INBOX.AI-Review`
- worker reprocessed them through the standard production flow

### 4. Hardened deterministic rules for newsletters

Updated [src/mail_ai_agent/rule_engine.py](../src/mail_ai_agent/rule_engine.py) so obvious newsletter/promotional traffic no longer depends on LLM:

- added matching for newsletter-style sender patterns such as `newsletter@`
- added promotional CTA phrases such as:
  - `kup teraz`
  - `sprawdź` / `sprawdz`
  - `specjalnie dla ciebie`

This specifically addressed historical Rituals newsletters that were repeatedly falling into `uncertain`.

## Verification

### Tests

- admin/state tests: `19 passed`
- rule/admin/llm regression tests: `28 passed`

### Production checks

`bash scripts/prod_canary.sh` after remediation:

- `ok: true`
- `mailboxes_loaded: 8`
- `uncertain: 0`
- `failed: 0`
- `cleanup_pending: 0`

State summary after remediation:

- `processed=375`
- `uncertain=0`
- `failed=0`
- `cleanup_pending=0`

## Operational Outcome

The mailbox estate is back to a clean operational state:

- malformed historical blocker removed
- historical `uncertain` queue cleared
- deterministic rules improved to reduce future newsletter-related LLM failures
- worker and healthcheck returned to green

## Follow-up

The admin CLI should remain available for future narrow remediation tasks instead of using ad hoc SQL or manual IMAP operations.
