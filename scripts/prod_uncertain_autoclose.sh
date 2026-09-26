#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
ENV_FILE="${ENV_FILE:-.env.multi.prod}"
STATE_DB="${STATE_DB:-data/multi-prod-state.sqlite}"
AUDIT_LOG="${AUDIT_LOG:-logs/multi-prod-audit.jsonl}"
OLDER_THAN_HOURS="${OLDER_THAN_HOURS:-24}"
LIMIT="${LIMIT:-500}"

"$PYTHON_BIN" -m mail_ai_agent.uncertain_autoclose_cli \
  --env-file "$ENV_FILE" \
  --state-db "$STATE_DB" \
  --audit-log "$AUDIT_LOG" \
  --older-than-hours "$OLDER_THAN_HOURS" \
  --limit "$LIMIT"
