# AI Mail Triage MVP

Local email triage worker built around:

- IMAP
- Python
- Ollama for local classification
- SQLite for workflow state and leases
- JSONL audit logging

## What is included in the public repo

This repository is prepared for public sharing:

- real `.env` files are ignored
- real mailbox manifests are ignored
- runtime data, logs, drafts, and generated output are ignored
- only example configuration files are kept in git

## Setup

1. Create a virtual environment:

```bash
python3 -m venv .venv
```

2. Install dependencies:

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
```

3. Create `.env` from `.env.example` and fill in real values.
4. For safer IMAP testing, create `.env.test` from `.env.test.example`.
5. For multi-mailbox mode, copy `config/mailboxes.example.json` to a local manifest such as `config/mailboxes.local.json` and point `MAILBOXES_CONFIG_PATH` at that file.

## Single-mailbox mode

Required settings:

- `IMAP_HOST`
- `IMAP_USER`
- `IMAP_PASS`

Run:

```bash
.venv/bin/python -m mail_ai_agent.cli --json
```

Use a custom env file:

```bash
.venv/bin/python -m mail_ai_agent.cli --env-file .env.test --json
```

## Multi-mailbox mode

Set `MAILBOXES_CONFIG_PATH` in your env file to your local mailbox manifest.

Start from the example:

- [config/mailboxes.example.json](config/mailboxes.example.json)

Prepare local files:

```bash
cp .env.multi.test.example .env.multi.test
cp config/mailboxes.example.json config/mailboxes.local.json
```

Then update `.env.multi.test` so `MAILBOXES_CONFIG_PATH=config/mailboxes.local.json`, fill real credentials in the local manifest, and run:

```bash
.venv/bin/python -m mail_ai_agent.cli --env-file .env.multi.test --json
```

## Runtime behavior

- processed mail is moved with IMAP `copy -> mark_deleted -> expunge`
- deterministic complaint rules add `\\Flagged`
- if copy succeeds but source cleanup fails, state is stored as `cleanup_pending` with action `move_copy_succeeded_cleanup_pending`
- worker runs an automatic cleanup pass for `cleanup_pending` records before processing new candidates
- `cleanup_cli` can still retry source-folder cleanup manually for pending records
- cleanup pass verifies stored `UIDVALIDITY` before deleting from source; mismatches are skipped and logged
- IMAP operations use retry and reconnect with `IMAP_MAX_RETRIES` and `IMAP_RETRY_BACKOFF_SECONDS`
- candidate selection is configurable with `IMAP_SEARCH_CRITERION` and capped by `IMAP_FETCH_LIMIT`
- `IMAP_SEARCH_CRITERION` is tokenized with shell-like quoting, so simple quoted criteria such as `TEXT "hello world"` are supported
- fetched candidates now carry IMAP `UIDVALIDITY` into persisted workflow state
- state stores both an identity fingerprint and a content fingerprint; content fingerprint is used only as fallback deduplication when `Message-ID` is missing
- `DRY_RUN=true` is a simulation mode: no IMAP mutation, no terminal SQLite state, no draft files
- cleanup candidate selection now targets only explicit `cleanup_pending` records; legacy cleanup heuristics are no longer part of the main runtime path

Manual cleanup preview:

```bash
.venv/bin/python -m mail_ai_agent.cleanup_cli
```

Apply cleanup and expunge:

```bash
.venv/bin/python -m mail_ai_agent.cleanup_cli --apply --expunge
```

`cleanup_cli --expunge` and the automatic cleanup pass use folder-level IMAP `EXPUNGE`. This assumes the worker owns cleanup behavior for the source folder and there are no unrelated `\Deleted` messages left there by another process.

## Bootstrap

```bash
bash scripts/bootstrap.sh
```

## Tests

Unit tests:

```bash
.venv/bin/pytest -q
```

Live Ollama test:

```bash
RUN_LIVE_OLLAMA_TESTS=1 .venv/bin/pytest tests/integration/test_ollama_live.py -q
```

Live IMAP test:

```bash
RUN_LIVE_IMAP_TESTS=1 \
LIVE_IMAP_HOST=mail.example.com \
LIVE_IMAP_USER=user@example.com \
LIVE_IMAP_PASS=change-me \
LIVE_IMAP_SOURCE_FOLDER=INBOX.Test-AI-Review \
.venv/bin/pytest tests/integration/test_imap_live.py -q
```

Do not point live IMAP tests at a production source folder.
