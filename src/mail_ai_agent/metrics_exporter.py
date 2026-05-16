from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from pathlib import Path
from typing import Callable, cast

from .healthcheck_cli import build_health_payload
from .quality_learning_cli import build_quality_learning_payload
from .quality_report import build_quality_payload
from .reporting import summarize_state

LOGGER = logging.getLogger(__name__)


def build_metrics_payload(
    *,
    state_db: Path,
    audit_log: Path,
    env_file: Path | None = None,
    stdout_log: Path | None,
    stderr_log: Path | None,
    recent_audit_limit: int,
    recent_audit_max_age_minutes: int,
    max_uncertain: int,
) -> str:
    state_summary = summarize_state(state_db)
    health = build_health_payload(
        state_db=state_db,
        audit_log=audit_log,
        env_file=env_file,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        recent_audit_limit=recent_audit_limit,
        recent_audit_max_age_minutes=recent_audit_max_age_minutes,
        max_uncertain=max_uncertain,
    )
    quality = build_quality_payload(audit_log)
    learning = build_quality_learning_payload(state_db=state_db, audit_log=audit_log)
    autotune_source = _latest_autotune_source_dir()
    deploy_summary = _latest_autotune_deploy_summary()
    operational_health_status = _operational_health_status(health)
    state_mailboxes = cast(dict[str, int], state_summary.get("mailboxes", {}))
    uncertain_by_mailbox = cast(dict[str, int], state_summary.get("uncertain_by_mailbox", {}))
    failed_by_mailbox = cast(dict[str, int], state_summary.get("failed_by_mailbox", {}))

    lines = [
        "# HELP mailai_health_ok 1 when the mail AI system is healthy.",
        "# TYPE mailai_health_ok gauge",
        f"mailai_health_ok {1 if health['ok'] else 0}",
        "# HELP operational_health_status Cross-service operational health status (0=normal, 1=watch, 2=elevated).",
        "# TYPE operational_health_status gauge",
        f"operational_health_status {operational_health_status}",
    ]

    for key, value in cast(dict[str, object], health["state"]).items():
        lines.extend(
            [
                f"# HELP mailai_state_{key} Current {key} value from SQLite state.",
                f"# TYPE mailai_state_{key} gauge",
                f"mailai_state_{key} {int(cast(int, value))}",
            ]
        )

    summary = quality["summary"]
    for key in ["records", "uncertain", "failed", "cleanup_pending", "llm_routed", "rule_routed", "routed_records"]:
        lines.extend(
            [
                f"# HELP mailai_quality_{key} Current {key} value from audit-derived quality summary.",
                f"# TYPE mailai_quality_{key} gauge",
                f"mailai_quality_{key} {float(summary[key])}",
            ]
        )

    lines.extend(
        [
            "# HELP mailai_quality_llm_share Share of audit records routed by LLM.",
            "# TYPE mailai_quality_llm_share gauge",
            f"mailai_quality_llm_share {summary['llm_share']}",
            "# HELP mailai_quality_rule_share Share of audit records routed by deterministic rules.",
            "# TYPE mailai_quality_rule_share gauge",
            f"mailai_quality_rule_share {summary['rule_share']}",
        ]
    )

    action_counts = cast(dict[str, int], quality.get("by_action", {}))
    expected_skip_actions = ("skip_already_done", "skip_conflict", "skip_locked")
    processing_failure_actions = (
        "failed",
        "failed_parse",
        "failed_classify",
        "failed_route",
        "mailbox_failed",
        "imap_auth_failed",
        "move_copy_succeeded_cleanup_pending",
        "move_route_uncertain_llm_failure",
        "move_route_uncertain_parse_failure",
        "cleanup_source_already_done_failed",
    )
    expected_skips = sum(int(action_counts.get(key, 0)) for key in expected_skip_actions)
    processing_failures = sum(int(action_counts.get(key, 0)) for key in processing_failure_actions)
    lines.extend(
        [
            "# HELP mailai_quality_expected_skips Expected non-actionable skips from idempotency/locking.",
            "# TYPE mailai_quality_expected_skips gauge",
            f"mailai_quality_expected_skips {float(expected_skips)}",
            "# HELP mailai_quality_processing_failures Actionable processing failures across pipeline/runtime.",
            "# TYPE mailai_quality_processing_failures gauge",
            f"mailai_quality_processing_failures {float(processing_failures)}",
        ]
    )

    parse_error_total = int(quality["by_category"].get("parse_error", 0))

    lines.extend(
        [
            "# HELP mailai_processing_events_total Audit-derived processing event totals by outcome.",
            "# TYPE mailai_processing_events_total counter",
            f'mailai_processing_events_total{{outcome="llm_routed"}} {float(summary["llm_routed"])}',
            f'mailai_processing_events_total{{outcome="rule_routed"}} {float(summary["rule_routed"])}',
            f'mailai_processing_events_total{{outcome="uncertain"}} {float(summary["uncertain"])}',
            f'mailai_processing_events_total{{outcome="failed"}} {float(summary["failed"])}',
            f'mailai_processing_events_total{{outcome="parse_error"}} {float(parse_error_total)}',
            f'mailai_processing_events_total{{outcome="cleanup_pending"}} {float(summary["cleanup_pending"])}',
        ]
    )

    lines.extend(_labelled_metrics("mailai_mailbox_records", "Unique messages by mailbox from SQLite state.", state_mailboxes, "mailbox_id"))
    lines.extend(
        _labelled_metrics(
            "mailai_mailbox_uncertain_current",
            "Current uncertain records by mailbox from SQLite state.",
            uncertain_by_mailbox,
            "mailbox_id",
        )
    )
    lines.extend(
        _labelled_metrics(
            "mailai_mailbox_failed_current",
            "Current failed records by mailbox from SQLite state.",
            failed_by_mailbox,
            "mailbox_id",
        )
    )
    lines.extend(_labelled_metrics("mailai_mailbox_audit_events", "Audit log events by mailbox in audit-derived quality summary.", quality["by_mailbox"], "mailbox_id"))
    lines.extend(_labelled_metrics("mailai_category_records", "Records by category in audit-derived quality summary.", quality["by_category"], "category"))
    lines.extend(_labelled_metrics("mailai_action_records", "Records by action in audit-derived quality summary.", quality["by_action"], "action"))
    lines.extend(_labelled_metrics("mailai_target_folder_records", "Records by target folder in audit-derived quality summary.", quality["by_target_folder"], "target_folder"))
    lines.extend(_labelled_metrics("mailai_route_source_records", "Records by route source in audit-derived quality summary.", quality["by_route_source"], "route_source"))
    lines.extend(
        _labelled_metrics(
            "mailai_quality_learning_proposals",
            "Current quality-learning proposal counts by kind.",
            cast(dict[str, int], learning.get("proposal_counts", {})),
            "kind",
        )
    )
    if autotune_source:
        escaped = autotune_source.replace("\\", "\\\\").replace('"', '\\"')
        lines.extend(
            [
                "# HELP mailai_autotune_signals_source Latest weekly autotune signals source directory (label-only indicator).",
                "# TYPE mailai_autotune_signals_source gauge",
                f'mailai_autotune_signals_source{{source="{escaped}"}} 1',
            ]
        )
    counts: dict[str, int] = {}
    soft_share = 0.0
    rollout_aborted = False
    if deploy_summary:
        summary_counts = deploy_summary.get("verification_mode_counts", {})
        if isinstance(summary_counts, dict):
            counts = {str(k): int(v) for k, v in summary_counts.items()}
        soft_value = deploy_summary.get("soft_pass_share")
        if isinstance(soft_value, (int, float)):
            soft_share = float(soft_value)
        aborted_value = deploy_summary.get("rollout_aborted")
        if isinstance(aborted_value, bool):
            rollout_aborted = aborted_value
    lines.extend(
        _labelled_metrics(
            "mailai_sieve_deploy_verifications",
            "Latest weekly autotune deploy verification mode counts.",
            counts,
            "mode",
        )
    )
    lines.extend(
        [
            "# HELP mailai_sieve_deploy_soft_pass_share Latest weekly autotune deploy soft-pass share.",
            "# TYPE mailai_sieve_deploy_soft_pass_share gauge",
            f"mailai_sieve_deploy_soft_pass_share {soft_share}",
            "# HELP mailai_sieve_deploy_rollout_aborted 1 when latest weekly autotune deploy aborted after canary.",
            "# TYPE mailai_sieve_deploy_rollout_aborted gauge",
            f"mailai_sieve_deploy_rollout_aborted {1 if rollout_aborted else 0}",
        ]
    )

    return "\n".join(lines) + "\n"


def _operational_health_status(health: dict[str, object]) -> int:
    state = cast(dict[str, object], health.get("state", {}))
    issues = [str(issue) for issue in cast(list[object], health.get("issues", []))]

    elevated_prefixes = ("state_failed=", "state_cleanup_pending=", "config_error=")
    elevated_markers = (
        "recent mailbox_failed present in audit log",
        "recent imap_auth_failed present in audit log",
        "recent cleanup_uidvalidity_mismatch present in audit log",
        "recent folder-level expunge refusal present in audit log",
    )
    if any(issue.startswith(elevated_prefixes) for issue in issues) or any(marker in issues for marker in elevated_markers):
        return 2
    if int(cast(int, state.get("uncertain", 0))) > 0 or issues:
        return 1
    return 0


def _labelled_metrics(metric_name: str, help_text: str, mapping: dict[str, int], label_name: str) -> list[str]:
    escaped_help = help_text.replace("\\", "\\\\").replace("\n", "\\n")
    lines = [f"# HELP {metric_name} {escaped_help}", f"# TYPE {metric_name} gauge"]
    for key, value in sorted(mapping.items()):
        escaped_key = str(key).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        lines.append(f'{metric_name}{{{label_name}="{escaped_key}"}} {int(value)}')
    return lines


def _latest_autotune_source_dir(log_dir: Path = Path("logs/weekly-autotune")) -> str | None:
    files = sorted(log_dir.glob("weekly-autotune-*.quality.json"), reverse=True)
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        source = payload.get("signals_source_dir")
        if isinstance(source, str) and source.strip():
            return source.strip()
    return None


def _latest_autotune_deploy_summary(log_dir: Path = Path("logs/weekly-autotune")) -> dict[str, object] | None:
    files = sorted(log_dir.glob("weekly-autotune-*.quality.json"), reverse=True)
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        deploy = payload.get("deploy")
        if not isinstance(deploy, dict):
            quality_payload = payload.get("quality_payload")
            if isinstance(quality_payload, dict):
                deploy = quality_payload.get("deploy")
        if not isinstance(deploy, dict):
            continue

        results = deploy.get("results", [])
        if not isinstance(results, list):
            results = []
        counts: dict[str, int] = {}
        soft = 0
        total = 0
        for item in results:
            if not isinstance(item, dict):
                continue
            mode = str(item.get("verification_mode") or "failed")
            counts[mode] = counts.get(mode, 0) + 1
            if mode == "soft_pass":
                soft += 1
            total += 1
        return {
            "verification_mode_counts": counts,
            "soft_pass_share": (soft / total) if total else 0.0,
            "rollout_aborted": bool(deploy.get("rollout_aborted", False)),
        }
    return None


def serve_metrics(
    *,
    host: str,
    port: int,
    payload_builder: Callable[[], str],
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path not in {"/metrics", "/metrics/"}:
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = payload_builder().encode("utf-8")
            except Exception:
                LOGGER.exception("metrics payload generation failed")
                body = b"metrics payload generation failed\n"
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prometheus metrics exporter for AI Mail Triage")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=9177, help="Port to bind")
    parser.add_argument("--audit-log", default="logs/audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--state-db", default="data/state.sqlite", help="Path to SQLite state DB")
    parser.add_argument("--env-file", default=None, help="Optional env file path")
    parser.add_argument("--stdout-log", default=None, help="Optional stdout log path")
    parser.add_argument("--stderr-log", default=None, help="Optional stderr log path")
    parser.add_argument("--recent-audit-limit", type=int, default=50, help="How many recent audit records to inspect")
    parser.add_argument("--recent-audit-max-age-minutes", type=int, default=15, help="Only inspect audit records newer than this many minutes")
    parser.add_argument("--max-uncertain", type=int, default=0, help="Maximum tolerated uncertain rows in state")
    parser.add_argument("--oneshot", action="store_true", help="Print metrics once and exit")
    args = parser.parse_args()

    builder: Callable[[], str] = lambda: build_metrics_payload(
        state_db=Path(args.state_db),
        audit_log=Path(args.audit_log),
        env_file=Path(args.env_file) if args.env_file else None,
        stdout_log=Path(args.stdout_log) if args.stdout_log else None,
        stderr_log=Path(args.stderr_log) if args.stderr_log else None,
        recent_audit_limit=args.recent_audit_limit,
        recent_audit_max_age_minutes=args.recent_audit_max_age_minutes,
        max_uncertain=args.max_uncertain,
    )

    if args.oneshot:
        print(builder(), end="")
        return

    serve_metrics(host=args.host, port=args.port, payload_builder=builder)


if __name__ == "__main__":
    main()
