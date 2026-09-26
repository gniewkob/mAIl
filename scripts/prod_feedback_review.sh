#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
ENV_FILE="${ENV_FILE:-.env.multi.prod}"
AUDIT_LOG="${AUDIT_LOG:-logs/multi-prod-audit.jsonl}"
STATE_DB="${STATE_DB:-data/multi-prod-state.sqlite}"
FEEDBACK_LOG="${FEEDBACK_LOG:-logs/quality-feedback.jsonl}"
CANDIDATES_PATH="${CANDIDATES_PATH:-logs/feedback-candidates-daily.json}"
WINDOW_HOURS="${WINDOW_HOURS:-24}"
LIMIT="${LIMIT:-20}"
LOW_CONF_THRESHOLD="${LOW_CONF_THRESHOLD:-0.96}"
MAILBOX_ID="${MAILBOX_ID:-review_session}"
IMAP_PER_FOLDER_LIMIT="${IMAP_PER_FOLDER_LIMIT:-20}"

"$PYTHON_BIN" -m mail_ai_agent.feedback_sampling_cli \
  --env-file "$ENV_FILE" \
  --audit-log "$AUDIT_LOG" \
  --state-db "$STATE_DB" \
  --output "$CANDIDATES_PATH" \
  --imap-folder "INBOX.AI-Uncertain" \
  --imap-folder "INBOX.AI-Review" \
  --imap-per-folder-limit "$IMAP_PER_FOLDER_LIMIT" \
  --window-hours "$WINDOW_HOURS" \
  --limit "$LIMIT" \
  --low-conf-threshold "$LOW_CONF_THRESHOLD"

echo
"$PYTHON_BIN" -m mail_ai_agent.feedback_review_session_cli \
  --candidates "$CANDIDATES_PATH" \
  --feedback-log "$FEEDBACK_LOG" \
  --mailbox-id "$MAILBOX_ID" \
  --limit "$LIMIT"
