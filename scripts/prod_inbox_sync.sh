#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
ENV_FILE="${ENV_FILE:-.env.multi.prod}"
MODE="${1:-report}"
LIMIT_PER_FOLDER="${LIMIT_PER_FOLDER:-0}"
TAG_PREFIX="${TAG_PREFIX:-\$AI_}"
MAILBOX_ID="${MAILBOX_ID:-}"

APPLY_FLAG=""
MISSING_POLICY="report"

case "$MODE" in
  report)
    MISSING_POLICY="report"
    ;;
  repair)
    MISSING_POLICY="copy"
    APPLY_FLAG="--apply"
    ;;
  prune)
    MISSING_POLICY="delete"
    APPLY_FLAG="--apply"
    ;;
  *)
    echo "Usage: $(basename "$0") [report|repair|prune]" >&2
    exit 1
    ;;
esac

STAMP="$(date +%Y%m%d-%H%M%S)"
OUTPUT_PATH="logs/tag-migration-${MODE}-${STAMP}.json"

CMD=(
  "$PYTHON_BIN" -m mail_ai_agent.migrate_to_tag_architecture_cli
  --env-file "$ENV_FILE"
  --missing-policy "$MISSING_POLICY"
  --limit-per-folder "$LIMIT_PER_FOLDER"
  --tag-prefix "$TAG_PREFIX"
)

if [[ -n "$MAILBOX_ID" ]]; then
  CMD+=(--mailbox-id "$MAILBOX_ID")
fi
if [[ -n "$APPLY_FLAG" ]]; then
  CMD+=("$APPLY_FLAG")
fi
CMD+=(--output "$OUTPUT_PATH")

"${CMD[@]}"

echo "[OK] Mode=$MODE output=$OUTPUT_PATH"
