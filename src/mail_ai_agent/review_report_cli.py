from __future__ import annotations

import argparse
from pathlib import Path

from .review_report import build_review_rows, export_review_csv, review_report_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual review report for dry-run audit logs")
    parser.add_argument("--audit-log", default="logs/test-audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--export-csv", default=None, help="Optional destination CSV")
    args = parser.parse_args()

    audit_path = Path(args.audit_log)
    if args.export_csv:
        export_review_csv(build_review_rows(audit_path), Path(args.export_csv))
    print(review_report_json(audit_path))


if __name__ == "__main__":
    main()
