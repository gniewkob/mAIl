# Routing: one copy per message (`hybrid`) — 2026-09-26

## Why
`copy_plus_tag` kept every classified message twice: the original in INBOX and a copy in its
category folder (22,759 pairs across 8 mailboxes on 2026-09-26). IMAP treats them as two
independent messages, so deleting one never deleted the other. The nightly
`inbox.sync.repair` job then copied category copies back into INBOX whenever the user had
deleted the INBOX original — and also copied everything from `Junk` (SpamSieve/server spam)
back into INBOX. That was the source of "deleted mail keeps coming back, unread".

## What changed
- `inbox.sync.repair` is **disabled permanently** (plist in `ops/disabled-launchd/`).
  `--missing-policy copy` is a one-off migration tool; the CLI default is now `report`.
- `Junk` is excluded from INBOX<->category sync (never restored, never pruned).
- `prune` has guards: refuses an empty INBOX listing; skips a folder if > 300 or > 50 % of
  it would be deleted; skips any mailbox whose effective strategy is not `copy_plus_tag`.
- New routing strategy **`hybrid`** (global `ROUTING_STRATEGY` or per mailbox
  `"routing_strategy"` in the manifest):
  - `HYBRID_MOVE_CATEGORIES` (default `newsletter,offer,spam,system,billing`) are **moved**
    to their folder — single copy there;
  - everything else (appointment, question, complaint, other) and anything uncertain stays in
    INBOX with only the `$AI_<category>` keyword;
  - LLM/parse failure paths no longer copy to `AI-Uncertain`; the message stays in INBOX.
  Result: one copy per message, so a delete or a move to Junk on any device is final.
- The test worker `com.salonbw.mailai.multi.test` (dry-run over prod mailboxes every 5 min,
  unbounded audit log) is disabled; its log was archived to `logs/archive/*.zst`.

## Switching a mailbox to hybrid
1. Set `"routing_strategy": "hybrid"` for the mailbox in `config/mailboxes.active.json`.
2. Report: `PYTHONPATH=src .venv/bin/python ops/dedupe_to_hybrid.py --mailbox-id <id>`
3. Apply:  `... ops/dedupe_to_hybrid.py --mailbox-id <id> --apply`
   (noise pairs: keep folder copy + sync \Seen/\Flagged/\Answered, delete INBOX copy;
   human pairs: keep INBOX original, delete folder copy). Safe to re-run.

Pilot: `gniewko_bodora` switched 2026-09-26 (INBOX 6,655 -> 2,159, 0 errors).

## Rollback
Remove the mailbox's `routing_strategy` (falls back to global `copy_plus_tag`). Mail already
moved stays in its folder; nothing is lost. Code backups: `src/mail_ai_agent/*.bak.20260926T110056Z`.

## Server-side sieve (mydevil) — fixed 2026-09-26
The active server script (deployed 2026-05-26, never updated since: the weekly autotune job
generates scripts but runs without `--deploy`) used `fileinto :copy` for Junk, Billing, System
and Newsletter, so the server itself delivered two copies of every such message.
- `sieve_unify_cli._render` now emits a **spam gate** for single-copy mailboxes (`hybrid`/`move`):
  only `X-Spam-Flag: YES` is moved to Junk (no `:copy`); everything else is kept for mAIl.
  The keyword pre-filters are not applied server-side: substring matches ("ing", "log", "plus")
  are too loose to move mail out of INBOX unchecked.
- Deployed to all 8 mailboxes with `--strict-verify` and an independent GETSCRIPT read-back.
- Previous server scripts: `logs/sieve-server-snapshot-20260926/` (rollback = redeploy that dir).
- Same-folder duplicates left by the old rule were removed with `ops/dedupe_same_folder.py`.

## Automated-sender guard + INBOX re-file — 2026-09-26
The LLM classifies by topic, so no-reply shops, utilities, parking operators and calendar
notifications were tagged appointment/complaint/other and stayed in INBOX under `hybrid`.
- `automated_sender.py`: after the LLM, a human category (appointment/question/complaint/other)
  from an automated sender (no-reply/notification/powiadomienia/newsletter local part,
  `Auto-Submitted`) becomes `system`; bulk mail (`List-Unsubscribe`, `List-ID`,
  `Precedence: bulk/list`) becomes `newsletter`. Non-human LLM categories are kept.
  Exception list: `AUTOMATED_SENDER_KEEP=versum,booksy` (substrings of sender address).
- `ops/refile_inbox.py` applied the same logic to existing INBOX mail (761 moved on 2026-09-26),
  plus noise-tagged INBOX mail that had no folder copy.

## Spam reports to mydevil — 2026-09-26
mydevil's SpamAssassin learns only from explicit reports. `spam_report_cli` (launchd
`com.mailai.spam.report.daily`, 04:10) forwards Junk messages NOT flagged by the server
(`X-Spam-Flag: YES` absent) to spam@mydevil.net as message/rfc822 attachments, from each
mailbox's own account. Grace period 24 h (rescue false positives before they are reported),
window 8 days, max 40/mailbox/night, each message once (`data/spam-reported.json`, hashes only).
Disable: `launchctl bootout gui/$(id -u)/com.mailai.spam.report.daily`.

## Booksy + cross-folder duplicates + folder reconciliation — 2026-09-26 (evening)
- Server SpamAssassin scores Booksy booking notifications ~6.5 (> 5.0 = spam). After the
  spam-gate sieve they would have been MOVED to Junk. Fixed in three places:
  sieve rule 0 (`address :domain :is "from" ["booksy.com"]` -> keep, before the spam rule),
  rule_engine (trusted sender never becomes spam via X-Spam-* headers), spam_report_cli
  (trusted senders are never reported). Source: `AUTOMATED_SENDER_KEEP=booksy` in `.env.multi.prod`.
- `ops/consolidate_folders.py`: one copy per Message-ID across ALL folders (935 messages were in
  two category folders because the old sieve copy and mAIl's copy disagreed); Booksy bookings:
  upcoming visit -> INBOX ($AI_appointment), past -> INBOX.System archive.
- `ops/reconcile_with_state.py`: untagged folder copies (old keyword sieve) checked against mAIl's
  decision in the state DB: 9,349 agreed (tagged), 286 moved to mAIl's category, 359 never seen by
  mAIl (left as is).
All three ops scripts are report-only without `--apply` and safe to re-run.
