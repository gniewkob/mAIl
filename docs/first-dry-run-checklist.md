# First Dry-Run Checklist

## Before start

- Optional: run `bash scripts/bootstrap.sh`
- Create dedicated IMAP test folders:
  - `INBOX.Test-AI-Review`
  - `INBOX.Test-Appointments`
  - `INBOX.Test-Questions`
  - `INBOX.Test-Complaints`
  - `INBOX.Test-Other`
  - `INBOX.Test-AI-Uncertain`
  - optional: `INBOX.Test-Billing`, `INBOX.Test-System`
- Copy [`.env.test.example`](/Users/gniewkob/Repos/priv/mAIl/.env.test.example) to `.env.test` and fill real credentials.
- Confirm `DRY_RUN=true` in `.env.test`.
- Confirm local Ollama is running and the configured model is pulled.
- Put a few non-production emails into `INBOX.Test-AI-Review`.

## Safe execution order

1. Run unit tests:

```bash
.venv/bin/pytest -q
```

2. Run live Ollama test:

```bash
RUN_LIVE_OLLAMA_TESTS=1 .venv/bin/pytest tests/integration/test_ollama_live.py -q
```

3. Run live IMAP fetch test:

```bash
RUN_LIVE_IMAP_TESTS=1 \
LIVE_IMAP_HOST=mail.mydevil.net \
LIVE_IMAP_USER=kontakt@salon-bw.pl \
LIVE_IMAP_PASS='...' \
LIVE_IMAP_SOURCE_FOLDER=INBOX.Test-AI-Review \
.venv/bin/pytest tests/integration/test_imap_live.py -q
```

4. Run the worker in dry-run mode:

```bash
.venv/bin/python -m mail_ai_agent.cli --env-file .env.test --json
```

## What to verify after dry-run

- No mail moved between IMAP folders.
- No mail marked as `\Seen` unexpectedly.
- `logs/test-audit.jsonl` contains one entry per processed attempt.
- `data/test-state.sqlite` is not used to store terminal processing state for simulated messages.
- No draft files are created in dry-run mode.
- Audit entries end as `simulated`, not `processed` or `uncertain`.

## Only after that

- Keep using test folders.
- Flip `DRY_RUN=false` in `.env.test`.
- Run the worker again and verify real move behavior plus cleanup reporting on test folders before touching production.
