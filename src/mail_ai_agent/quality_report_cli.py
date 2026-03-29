from __future__ import annotations

import argparse
import json
from pathlib import Path

from .quality_report import build_quality_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Quality dashboard for AI Mail Triage audit logs")
    parser.add_argument("--audit-log", default="logs/test-audit.jsonl", help="Path to audit JSONL")
    args = parser.parse_args()

    payload = build_quality_payload(Path(args.audit_log))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
