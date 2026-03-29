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

"$PYTHON_BIN" -m mail_ai_agent.healthcheck_cli \
  --state-db "$STATE_DB" \
  --audit-log "$AUDIT_LOG" \
  --stdout-log "$STDOUT_LOG" \
  --stderr-log "$STDERR_LOG" \
  --recent-audit-limit 50 \
  --max-uncertain 0
