#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
ENV_FILE="${ENV_FILE:-.env.multi.prod}"
STATE_DB="${STATE_DB:-data/multi-prod-state.sqlite}"
AUDIT_LOG="${AUDIT_LOG:-logs/multi-prod-audit.jsonl}"
STDOUT_LOG="${STDOUT_LOG:-logs/launchd-multi-prod-stdout.log}"
STDERR_LOG="${STDERR_LOG:-logs/launchd-multi-prod-stderr.log}"
MAX_UNCERTAIN="${MAX_UNCERTAIN:-3}"
MAX_UNCERTAIN_ERROR="${MAX_UNCERTAIN_ERROR:-0}"
MAX_UNCERTAIN_EXPLORATION="${MAX_UNCERTAIN_EXPLORATION:-$MAX_UNCERTAIN}"

"$PYTHON_BIN" -m mail_ai_agent.healthcheck_cli \
  --env-file "$ENV_FILE" \
  --state-db "$STATE_DB" \
  --audit-log "$AUDIT_LOG" \
  --stdout-log "$STDOUT_LOG" \
  --stderr-log "$STDERR_LOG" \
  --recent-audit-limit 50 \
  --max-uncertain "$MAX_UNCERTAIN" \
  --max-uncertain-error "$MAX_UNCERTAIN_ERROR" \
  --max-uncertain-exploration "$MAX_UNCERTAIN_EXPLORATION"
