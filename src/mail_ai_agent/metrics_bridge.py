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


def _read_token_file(path: Path | None) -> str | None:
    if path is None:
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None

def serve_bridge(
    *,
    host: str,
    port: int,
    payload_builder: Callable[[], str],
    trigger_token: str | None,
    trigger_link_token: str | None = None,
    trigger_label: str,
    trigger_allowlist: set[str] | None = None,
    trigger_link_origin_allowlist: set[str] | None = None,
    trigger_cooldown_seconds: int = 60,
    trigger_link_unauthorized_cooldown_seconds: int = 15,
    trigger_audit_log: Path | None = None,
    trigger_audit_max_bytes: int = 2_000_000,
    trigger_audit_backup_count: int = 5,
    payload_refresh_seconds: int = 300,
) -> None:
    LOGGER.info("Bridge listening on http://%s:%d/metrics", host, port)
    serve_metrics(
        host=host,
        port=port,
        payload_builder=payload_builder,
        trigger_token=trigger_token,
        trigger_link_token=trigger_link_token,
        trigger_label=trigger_label,
        trigger_allowlist=trigger_allowlist,
        trigger_link_origin_allowlist=trigger_link_origin_allowlist,
        trigger_cooldown_seconds=trigger_cooldown_seconds,
        trigger_link_unauthorized_cooldown_seconds=trigger_link_unauthorized_cooldown_seconds,
        trigger_audit_log=trigger_audit_log,
        trigger_audit_max_bytes=trigger_audit_max_bytes,
        trigger_audit_backup_count=trigger_audit_backup_count,
        payload_refresh_seconds=payload_refresh_seconds,
    )


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
    parser.add_argument(
        "--max-uncertain-error",
        type=int,
        default=None,
        help="Maximum tolerated non-exploration uncertain rows (defaults to max_uncertain when omitted).",
    )
    parser.add_argument(
        "--max-uncertain-exploration",
        type=int,
        default=None,
        help="Maximum tolerated exploration uncertain rows (defaults to max_uncertain when omitted).",
    )
    parser.add_argument("--trigger-token", default=None, help="Optional token required for POST /autotune/trigger")
    parser.add_argument("--trigger-token-file", default=None, help="Optional file path with trigger token")
    parser.add_argument("--trigger-link-token", default=None, help="Optional token required for POST /autotune/trigger-link form body")
    parser.add_argument("--trigger-link-token-file", default=None, help="Optional file path with trigger-link token")
    parser.add_argument(
        "--trigger-label",
        default="com.mailai.autotune.weekly.prod",
        help="launchd label to kickstart when /autotune/trigger is called.",
    )
    parser.add_argument(
        "--trigger-allowlist",
        default="127.0.0.1,::1",
        help="Comma-separated IP allowlist for /autotune/trigger.",
    )
    parser.add_argument(
        "--trigger-link-origin-allowlist",
        default="http://127.0.0.1:9177,http://localhost:9177,http://127.0.0.1:3005,http://localhost:3005,http://127.0.0.1:3000,http://localhost:3000",
        help="Comma-separated Origin/Referer allowlist for /autotune/trigger-link.",
    )
    parser.add_argument("--trigger-cooldown-seconds", type=int, default=60, help="Cooldown between accepted trigger executions.")
    parser.add_argument(
        "--trigger-link-unauthorized-cooldown-seconds",
        type=int,
        default=15,
        help="Per-IP cooldown after unauthorized /autotune/trigger-link attempt.",
    )
    parser.add_argument("--trigger-audit-log", default="logs/autotune-trigger-audit.jsonl", help="Path to trigger audit JSONL.")
    parser.add_argument(
        "--trigger-audit-max-bytes",
        type=int,
        default=2_000_000,
        help="Rotate trigger audit log once it exceeds this size (0 disables).",
    )
    parser.add_argument(
        "--trigger-audit-backup-count",
        type=int,
        default=5,
        help="Number of rotated trigger audit log backups to keep.",
    )
    parser.add_argument(
        "--payload-refresh-seconds",
        type=int,
        default=300,
        help="How often to refresh heavy metrics payload in background (0 disables periodic refresh).",
    )
    args = parser.parse_args()

    max_uncertain_error = args.max_uncertain if args.max_uncertain_error is None else int(args.max_uncertain_error)
    max_uncertain_exploration = (
        args.max_uncertain if args.max_uncertain_exploration is None else int(args.max_uncertain_exploration)
    )

    payload_builder: Callable[[], str] = lambda: build_metrics_payload(
        state_db=Path(args.state_db),
        audit_log=Path(args.audit_log),
        env_file=Path(args.env_file) if args.env_file else None,
        stdout_log=Path(args.stdout_log) if args.stdout_log else None,
        stderr_log=Path(args.stderr_log) if args.stderr_log else None,
        recent_audit_limit=args.recent_audit_limit,
        recent_audit_max_age_minutes=args.recent_audit_max_age_minutes,
        max_uncertain=args.max_uncertain,
        max_uncertain_error=max_uncertain_error,
        max_uncertain_exploration=max_uncertain_exploration,
    )

    trigger_token = args.trigger_token
    if trigger_token is None and args.trigger_token_file:
        trigger_token = _read_token_file(Path(args.trigger_token_file))
    trigger_link_token = args.trigger_link_token
    if trigger_link_token is None and args.trigger_link_token_file:
        trigger_link_token = _read_token_file(Path(args.trigger_link_token_file))

    serve_bridge(
        host=args.host,
        port=args.port,
        payload_builder=payload_builder,
        trigger_token=trigger_token,
        trigger_link_token=trigger_link_token,
        trigger_label=args.trigger_label,
        trigger_allowlist={part.strip() for part in str(args.trigger_allowlist).split(",") if part.strip()},
        trigger_link_origin_allowlist={
            part.strip() for part in str(args.trigger_link_origin_allowlist).split(",") if part.strip()
        },
        trigger_cooldown_seconds=max(0, int(args.trigger_cooldown_seconds)),
        trigger_link_unauthorized_cooldown_seconds=max(1, int(args.trigger_link_unauthorized_cooldown_seconds)),
        trigger_audit_log=Path(args.trigger_audit_log),
        trigger_audit_max_bytes=max(0, int(args.trigger_audit_max_bytes)),
        trigger_audit_backup_count=max(1, int(args.trigger_audit_backup_count)),
        payload_refresh_seconds=max(0, int(args.payload_refresh_seconds)),
    )


if __name__ == "__main__":
    main()
