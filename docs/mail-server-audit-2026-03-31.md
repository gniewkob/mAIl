# Mail Server Audit 2026-03-31

Timestamp: `2026-03-31 20:57:34 CEST`

## Scope

This audit covered the live production mail server behind the multi-mailbox worker:

- IMAP connectivity and advertised capabilities
- ManageSieve availability and active scripts
- folder existence vs `mAIl` runtime configuration
- live routing targets used by `fileinto`
- migration of existing messages from legacy folders into the worker-owned source folder

The worker configuration used during the audit was:

- env file: [.env.multi.prod](/Users/gniewkob/Repos/priv/mAIl/.env.multi.prod)
- mailbox manifest: [config/mailboxes.active.json](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json)
- standard source folder: `INBOX.AI-Review`

## What Was Wrong

Before remediation, the mail server configuration was inconsistent with the worker:

- `mAIl` listened on `INBOX.AI-Review`
- active Sieve scripts on most mailboxes delivered to `AI-Review`
- one mailbox (`agentai@gliwicka111.pl`) had no active Sieve script
- one mailbox (`kontakt@salon-bw.pl`) had an older script using unsupported `expire`
- several scripts delivered to nonexistent folders:
  - `INBOX.Clients`
  - `INBOX.Newsletters`

Operational effect:

- the worker kept running normally, but had no new mail in its source folder
- new mail accumulated in `INBOX` or legacy folders instead of the worker-owned source folder

## Remediation Applied

### 1. Standardized active Sieve scripts

All active production scripts were updated so the default worker handoff is:

- `fileinto "INBOX.AI-Review";`

### 2. Fixed mailbox-specific issues

- `kontakt@salon-bw.pl`
  - replaced the legacy script with a clean Dovecot-compatible script
  - removed unsupported `expire`
  - standardized targets to `INBOX.Billing`, `INBOX.System`, `INBOX.AI-Review`

- `agentai@gliwicka111.pl`
  - uploaded and activated `main.sieve`
  - current behavior is a simple default route to `INBOX.AI-Review`

### 3. Removed remaining target mismatches

Scripts that still pointed to nonexistent folders were normalized:

- `INBOX.Newsletters` -> `INBOX.Other`
- `INBOX.Clients` -> `INBOX.AI-Review`

This was applied to:

- `aleksandra_bodora`
- `carmen_bodora`
- `gloria_bodora`
- `gniewko_bodora`
- `kontakt_bodora`
- `kontakt_gliwicka111`

### 4. Migrated messages from legacy folders

Existing messages were moved from top-level `AI-Review` into `INBOX.AI-Review` so the worker could process the backlog immediately.

Moved counts:

- `aleksandra_bodora`: `3`
- `carmen_bodora`: `1`
- `gloria_bodora`: `4`
- `gniewko_bodora`: `15`
- `kontakt_bodora`: `1`

No legacy `AI-Review` source folder existed for:

- `kontakt_salon_bw`
- `agentai_gliwicka111`

## Backups

The original server-side scripts were backed up locally before modification:

- [logs/sieve-backup-2026-03-31](/Users/gniewkob/Repos/priv/mAIl/logs/sieve-backup-2026-03-31)

## Final Verification

### ManageSieve service

- host: `mail0.mydevil.net`
- port: `4190`
- implementation: `Dovecot Pigeonhole`

### IMAP capabilities

Observed across mailboxes:

- `IMAP4REV1`
- `IDLE`
- `AUTH=PLAIN`
- `AUTH=LOGIN`
- no `UIDPLUS`

This matches the current production design where `imap_allow_folder_expunge: true` is explicitly enabled per mailbox in [config/mailboxes.active.json](/Users/gniewkob/Repos/priv/mAIl/config/mailboxes.active.json).

### Active script targets

After remediation, every active script points only to existing folders.

Per-mailbox result:

- `kontakt_salon_bw`
  - active script: `managesieve`
  - targets: `Junk`, `INBOX.Billing`, `INBOX.System`, `INBOX.AI-Review`

- `aleksandra_bodora`
- `carmen_bodora`
- `gloria_bodora`
- `gniewko_bodora`
- `kontakt_bodora`
- `kontakt_gliwicka111`
  - active script: `main.sieve`
  - targets: `Junk`, `INBOX.Billing`, `INBOX.System`, `INBOX.Other`, `INBOX.AI-Review`

- `agentai_gliwicka111`
  - active script: `main.sieve`
  - targets: `INBOX.AI-Review`

### Folder consistency

Final state of the server-side routing contract:

- every mailbox has `INBOX.AI-Review`
- every active Sieve script includes `INBOX.AI-Review`
- no active script points to a nonexistent target folder

## Runtime Follow-up

After the Sieve remediation:

- the production worker was kicked once manually
- new processing records appeared in [data/multi-prod-state.sqlite](/Users/gniewkob/Repos/priv/mAIl/data/multi-prod-state.sqlite)
- live processing resumed from `INBOX.AI-Review`

Observed runtime note:

- at least one message was routed to `INBOX.AI-Uncertain` because the LLM returned an invalid payload shape

This is a separate LLM/runtime issue, not a mail-server routing issue.

## Current Conclusion

The mail server configuration is now aligned with the `mAIl` production worker standard:

- worker-owned source folder: `INBOX.AI-Review`
- all active Sieve scripts route into existing folders
- no remaining server-side folder-name mismatch was found at the end of the audit

The class of incident where Sieve silently routes outside the worker contract has been remediated.

## Final Folder Standardization

After the routing remediation, a second cleanup pass standardized the actual IMAP folder layout on all production mailboxes.

### Removed legacy and nonstandard folders

Only empty folders were deleted.

Removed legacy top-level folders:

- `AI-Review`
- `Billing`
- `System`
- `Newsletters`

Removed nonstandard mailbox-specific folders:

- `FINANSE`
- `INBOX.Oferty`
- `Clients`

### Restored missing standard folders

During the final verification pass, missing standard folders were recreated where necessary:

- `kontakt_bodora`
  - restored `INBOX.Questions`
  - restored `INBOX.System`

- `aleksandra_bodora`
  - created `Archive`

- `gloria_bodora`
  - created `Archive`

- `agentai_gliwicka111`
  - created `Drafts`
  - created `Archive`

### Final standard

At the end of the cleanup, every production mailbox exposes the same standard operational layout:

- `INBOX`
- `INBOX.AI-Review`
- `INBOX.AI-Uncertain`
- `INBOX.Appointments`
- `INBOX.Billing`
- `INBOX.Complaints`
- `INBOX.Other`
- `INBOX.Questions`
- `INBOX.System`
- `Sent`
- `Drafts`
- `Trash`
- `Junk`
- `Archive`

Final audit result:

- all 8 production mailboxes have the full standard folder set
- no extra nonstandard folders remain
- no required standard folder is missing
