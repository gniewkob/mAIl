from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Callable

from .metrics_exporter import build_metrics_payload, serve_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [mailai-bridge] %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("mailai-bridge")

def serve_bridge(*, host: str, port: int, payload_builder: Callable[[], str]) -> None:
    LOGGER.info("Bridge listening on http://%s:%d/metrics", host, port)
    serve_metrics(host=host, port=port, payload_builder=payload_builder)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prometheus metrics bridge for AI Mail Triage")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=9177, help="Port to bind")
    parser.add_argument("--audit-log", default="logs/audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--state-db", default="data/state.sqlite", help="Path to SQLite state DB")
    parser.add_argument("--env-file", default=None, help="Optional env file path")
    parser.add_argument("--stdout-log", default=None, help="Optional stdout log path")
    parser.add_argument("--stderr-log", default=None, help="Optional stderr log path")
    parser.add_argument("--recent-audit-limit", type=int, default=50, help="How many recent audit records to inspect")
    parser.add_argument(
        "--recent-audit-max-age-minutes",
        type=int,
        default=15,
        help="Only inspect audit records newer than this many minutes",
    )
    parser.add_argument("--max-uncertain", type=int, default=0, help="Maximum tolerated uncertain rows in state")
    args = parser.parse_args()

    payload_builder: Callable[[], str] = lambda: build_metrics_payload(
        state_db=Path(args.state_db),
        audit_log=Path(args.audit_log),
        env_file=Path(args.env_file) if args.env_file else None,
        stdout_log=Path(args.stdout_log) if args.stdout_log else None,
        stderr_log=Path(args.stderr_log) if args.stderr_log else None,
        recent_audit_limit=args.recent_audit_limit,
        recent_audit_max_age_minutes=args.recent_audit_max_age_minutes,
        max_uncertain=args.max_uncertain,
    )

    serve_bridge(host=args.host, port=args.port, payload_builder=payload_builder)


if __name__ == "__main__":
    main()
