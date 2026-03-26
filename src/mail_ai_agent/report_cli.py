from __future__ import annotations

import argparse
import json
from pathlib import Path

from .reporting import export_audit_csv, export_state_csv, load_audit_records, summarize_audit_records, summarize_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and state reporting for AI Mail Triage")
    parser.add_argument("--audit-log", default="logs/audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--state-db", default="data/state.sqlite", help="Path to SQLite state DB")
    parser.add_argument("--export-audit-csv", default=None, help="Optional destination CSV for audit log export")
    parser.add_argument("--export-state-csv", default=None, help="Optional destination CSV for state export")
    args = parser.parse_args()

    audit_path = Path(args.audit_log)
    state_path = Path(args.state_db)
    audit_records = load_audit_records(audit_path)

    if args.export_audit_csv:
        export_audit_csv(audit_records, Path(args.export_audit_csv))
    if args.export_state_csv:
        export_state_csv(state_path, Path(args.export_state_csv))

    payload = {
        "audit": summarize_audit_records(audit_records),
        "state": summarize_state(state_path),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
