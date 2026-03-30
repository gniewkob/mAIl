from __future__ import annotations

import argparse
import json

from .config import Settings
from .main import process_inbox


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Mail Triage worker")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional env file path. Defaults to .env if omitted.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the processing report as JSON.",
    )
    args = parser.parse_args()

    if args.env_file:
        settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    else:
        settings = Settings()
    report = process_inbox(settings)
    if args.json:
        print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    else:
        print(
            f"worker={report.worker_id} dry_run={report.dry_run} "
            f"mailboxes={report.mailboxes_processed} "
            f"candidates={report.candidates_seen} acquired={report.acquired} "
            f"processed={report.processed} uncertain={report.uncertain} "
            f"failed={report.failed} skipped={report.skipped} conflicts={report.conflicts} "
            f"lock_denied={report.worker_lock_denied}"
        )


if __name__ == "__main__":
    main()
