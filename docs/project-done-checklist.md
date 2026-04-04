# Project Done Checklist

Status on 2026-03-29:

- production folders were created on all active mailboxes
- `config/mailboxes.active.json` now points to production folders
- end-to-end `DRY_RUN=false` check passed on test folders
- multi-prod `launchd` worker is loaded
- metrics exporter is loaded
- Grafana dashboard `mAiL Overview` is live
- local suite passes: `96 passed, 2 skipped`

## Technical done

- multi-mailbox config works
- SQLite state is mailbox-scoped
- audit is mailbox-scoped
- dry-run works on active mailboxes
- launchd config exists for multi-mailbox test and production modes
- maintenance and cleanup paths are documented

## Operational done

- all target mailboxes have passwords
- all target mailboxes have `INBOX.Test-*`
- all target mailboxes have production folders:
  - `INBOX.AI-Review`
  - `INBOX.AI-Uncertain`
  - `INBOX.Appointments`
  - `INBOX.Questions`
  - `INBOX.Complaints`
  - `INBOX.Other`
  - `INBOX.Billing`
  - `INBOX.System`
- 1-2 day observation window in `DRY_RUN=true` is completed
- no unresolved `failed`
- complaint and offer routing looks acceptable

## Rollout done

- short `DRY_RUN=false` test completed on test folders only
- end-to-end move on test folders verified for:
  - billing -> `INBOX.Test-Billing`
  - question -> `INBOX.Test-Questions`
- cleanup procedure verified manually
- production env file prepared
- production launchd plist prepared
- production cutover completed

## Post-go-live

- run a production check after 1 hour
- run another production review after the first working day
- schedule a review after 2 weeks
- review false positives and false negatives
- review new repeated `uncertain`
- update rules before changing model thresholds

## Next stage after go-live

- keep runtime and rollout stable
- use Grafana and `quality_report_cli` as the main review tools
- build `rule suggestions` from repeated `route_source=llm` patterns
- expand deterministic rules only after tests and golden-set validation
- treat low `rule_share` as a tuning signal, not as a production incident
