from __future__ import annotations

import argparse
import json
from pathlib import Path

from .reporting import load_audit_records, summarize_audit_records, summarize_state


def build_status_payload(*, state_db: Path, audit_log: Path) -> dict[str, object]:
    state_summary = summarize_state(state_db)
    audit_summary = summarize_audit_records(load_audit_records(audit_log))
    statuses = state_summary.get("statuses", {})
    return {
        "state_db": str(state_db),
        "audit_log": str(audit_log),
        "records": state_summary.get("records", 0),
        "processed": statuses.get("processed", 0),
        "uncertain": statuses.get("uncertain", 0),
        "failed": statuses.get("failed", 0),
        "cleanup_pending": state_summary.get("cleanup_pending", 0),
        "simulated": audit_summary.get("simulated", 0),
        "cleanup_pass_processed": audit_summary.get("cleanup_pass_processed", 0),
        "cleanup_uidvalidity_mismatch": audit_summary.get("cleanup_uidvalidity_mismatch", 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compact operational status for AI Mail Triage")
    parser.add_argument("--audit-log", default="logs/audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--state-db", default="data/state.sqlite", help="Path to SQLite state DB")
    parser.add_argument("--json", action="store_true", help="Print the status payload as JSON")
    args = parser.parse_args()

    payload = build_status_payload(state_db=Path(args.state_db), audit_log=Path(args.audit_log))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(
        f"records={payload['records']} processed={payload['processed']} "
        f"uncertain={payload['uncertain']} failed={payload['failed']} "
        f"cleanup_pending={payload['cleanup_pending']} simulated={payload['simulated']} "
        f"cleanup_pass_processed={payload['cleanup_pass_processed']} "
        f"cleanup_uidvalidity_mismatch={payload['cleanup_uidvalidity_mismatch']}"
    )


if __name__ == "__main__":
    main()
