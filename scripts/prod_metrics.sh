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
METRICS_HOST="${METRICS_HOST:-127.0.0.1}"
METRICS_PORT="${METRICS_PORT:-9177}"
TRIGGER_ALLOWLIST="${TRIGGER_ALLOWLIST:-127.0.0.1,::1}"
TRIGGER_LINK_ORIGIN_ALLOWLIST="${TRIGGER_LINK_ORIGIN_ALLOWLIST:-http://127.0.0.1:9177,http://localhost:9177,http://127.0.0.1:3005,http://localhost:3005,http://127.0.0.1:3000,http://localhost:3000}"
TRIGGER_COOLDOWN_SECONDS="${TRIGGER_COOLDOWN_SECONDS:-60}"
TRIGGER_LINK_UNAUTHORIZED_COOLDOWN_SECONDS="${TRIGGER_LINK_UNAUTHORIZED_COOLDOWN_SECONDS:-15}"
TRIGGER_AUDIT_LOG="${TRIGGER_AUDIT_LOG:-logs/autotune-trigger-audit.jsonl}"
TRIGGER_AUDIT_MAX_BYTES="${TRIGGER_AUDIT_MAX_BYTES:-2000000}"
TRIGGER_AUDIT_BACKUP_COUNT="${TRIGGER_AUDIT_BACKUP_COUNT:-5}"
TRIGGER_LINK_TOKEN_FILE="${TRIGGER_LINK_TOKEN_FILE:-secrets/autotune_trigger.token}"
PAYLOAD_REFRESH_SECONDS="${PAYLOAD_REFRESH_SECONDS:-300}"
MAX_UNCERTAIN="${MAX_UNCERTAIN:-3}"
MAX_UNCERTAIN_ERROR="${MAX_UNCERTAIN_ERROR:-0}"
MAX_UNCERTAIN_EXPLORATION="${MAX_UNCERTAIN_EXPLORATION:-$MAX_UNCERTAIN}"

"$PYTHON_BIN" -m mail_ai_agent.metrics_bridge \
  --host "$METRICS_HOST" \
  --port "$METRICS_PORT" \
  --env-file "$ENV_FILE" \
  --state-db "$STATE_DB" \
  --audit-log "$AUDIT_LOG" \
  --stdout-log "$STDOUT_LOG" \
  --stderr-log "$STDERR_LOG" \
  --recent-audit-limit 50 \
  --recent-audit-max-age-minutes 15 \
  --max-uncertain "$MAX_UNCERTAIN" \
  --max-uncertain-error "$MAX_UNCERTAIN_ERROR" \
  --max-uncertain-exploration "$MAX_UNCERTAIN_EXPLORATION" \
  --trigger-allowlist "$TRIGGER_ALLOWLIST" \
  --trigger-link-origin-allowlist "$TRIGGER_LINK_ORIGIN_ALLOWLIST" \
  --trigger-cooldown-seconds "$TRIGGER_COOLDOWN_SECONDS" \
  --trigger-link-unauthorized-cooldown-seconds "$TRIGGER_LINK_UNAUTHORIZED_COOLDOWN_SECONDS" \
  --trigger-audit-log "$TRIGGER_AUDIT_LOG" \
  --trigger-audit-max-bytes "$TRIGGER_AUDIT_MAX_BYTES" \
  --trigger-audit-backup-count "$TRIGGER_AUDIT_BACKUP_COUNT" \
  --trigger-link-token-file "$TRIGGER_LINK_TOKEN_FILE" \
  --payload-refresh-seconds "$PAYLOAD_REFRESH_SECONDS"
