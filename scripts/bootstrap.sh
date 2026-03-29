#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/5] ensuring virtual environment"
if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

echo "[2/5] installing dependencies"
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .

echo "[3/5] running unit test suite"
.venv/bin/pytest -q

echo "[4/5] preparing local directories"
mkdir -p data logs drafts/pending drafts/test-pending output docs

echo "[5/5] reporting current local state"
.venv/bin/python -m mail_ai_agent.report_cli \
  --audit-log logs/test-audit.jsonl \
  --state-db data/test-state.sqlite \
  --export-audit-csv output/test-audit.csv \
  --export-state-csv output/test-state.csv || true

cat <<'EOF'

Bootstrap complete.

Next safe steps:
1. Copy .env.test.example to .env.test
2. Fill IMAP credentials or secret refs and confirm test folders only
3. Run:
   .venv/bin/python -m mail_ai_agent.preflight_cli --env-file .env.test
4. Then run:
   .venv/bin/python -m mail_ai_agent.cli --env-file .env.test --json

Optional live checks:
- RUN_LIVE_OLLAMA_TESTS=1 .venv/bin/pytest tests/integration/test_ollama_live.py -q
- RUN_LIVE_IMAP_TESTS=1 LIVE_IMAP_HOST=... LIVE_IMAP_USER=... LIVE_IMAP_PASS=... LIVE_IMAP_SOURCE_FOLDER=INBOX.Test-AI-Review .venv/bin/pytest tests/integration/test_imap_live.py -q

Production operations:
- bash scripts/prod_healthcheck.sh
- bash scripts/prod_canary.sh
- bash scripts/prod_alert.sh
- bash scripts/prod_metrics.sh

Quality operations:
- .venv/bin/python -m mail_ai_agent.quality_report_cli --audit-log logs/multi-prod-audit.jsonl
- .venv/bin/python -m mail_ai_agent.golden_set_cli tests/synthetic_data/golden_batch_001.json

EOF
