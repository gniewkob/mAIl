from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Callable, cast
from urllib.parse import parse_qs, urlparse

from .healthcheck_cli import build_health_payload
from .maintenance import rotate_audit_log
from .quality_learning_cli import build_quality_learning_payload
from .quality_report import build_quality_payload, build_quality_payload_between
from .reporting import summarize_state

LOGGER = logging.getLogger(__name__)

_EXPECTED_SKIP_ACTIONS = ("skip_already_done", "skip_conflict", "skip_locked")
_PROCESSING_FAILURE_ACTIONS = (
    "failed",
    "failed_parse",
    "failed_classify",
    "failed_route",
    "mailbox_failed",
    "imap_auth_failed",
    "move_copy_succeeded_cleanup_pending",
    "move_route_uncertain_llm_failure",
    "move_route_uncertain_parse_failure",
)


def _normalized_origin(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


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
    max_uncertain_error: int | None = None,
    max_uncertain_exploration: int | None = None,
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
        max_uncertain_error=max_uncertain_error,
        max_uncertain_exploration=max_uncertain_exploration,
    )
    quality = build_quality_payload(audit_log)
    quality_7d = build_quality_payload(audit_log, window_days=7)
    quality_14d = build_quality_payload(audit_log, window_days=14)
    quality_prev_7d = build_quality_payload_between(audit_log, start_days_ago=14, end_days_ago=7)
    learning = build_quality_learning_payload(state_db=state_db, audit_log=audit_log)
    autotune_source = _latest_autotune_source_dir()
    deploy_summary = _latest_autotune_deploy_summary()
    learner_summary = _latest_autotune_learner_summary()
    operational_health_status = _operational_health_status(health)
    state_mailboxes = cast(dict[str, int], state_summary.get("mailboxes", {}))
    uncertain_by_mailbox = cast(dict[str, int], state_summary.get("uncertain_by_mailbox", {}))
    uncertain_exploration_by_mailbox = cast(dict[str, int], state_summary.get("uncertain_exploration_by_mailbox", {}))
    uncertain_error_by_mailbox = cast(dict[str, int], state_summary.get("uncertain_error_by_mailbox", {}))
    failed_by_mailbox = cast(dict[str, int], state_summary.get("failed_by_mailbox", {}))
    configured_mailboxes = _configured_mailbox_ids(env_file)
    mailbox_metric_ids = set(state_mailboxes.keys()) | set(configured_mailboxes)
    for mailbox_id in mailbox_metric_ids:
        state_mailboxes.setdefault(mailbox_id, 0)
        uncertain_by_mailbox.setdefault(mailbox_id, 0)
        uncertain_exploration_by_mailbox.setdefault(mailbox_id, 0)
        uncertain_error_by_mailbox.setdefault(mailbox_id, 0)
        failed_by_mailbox.setdefault(mailbox_id, 0)

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
    expected_skips = sum(int(action_counts.get(key, 0)) for key in _EXPECTED_SKIP_ACTIONS)
    processing_failures = sum(int(action_counts.get(key, 0)) for key in _PROCESSING_FAILURE_ACTIONS)
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
    lines.extend(_window_quality_metrics(quality_7d, window_days=7))
    lines.extend(_window_quality_metrics(quality_14d, window_days=14))
    lines.extend(_quality_improvement_metrics(current_7d=quality_7d, previous_7d=quality_prev_7d))

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
    lines.extend(
        [
            "# HELP mailai_uncertain_exploration_backlog Current uncertain backlog caused by exploration routing.",
            "# TYPE mailai_uncertain_exploration_backlog gauge",
            f"mailai_uncertain_exploration_backlog {int(cast(int, state_summary.get('uncertain_exploration', 0)))}",
            "# HELP mailai_uncertain_error_backlog Current uncertain backlog caused by non-exploration/error paths.",
            "# TYPE mailai_uncertain_error_backlog gauge",
            f"mailai_uncertain_error_backlog {int(cast(int, state_summary.get('uncertain_error', 0)))}",
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
            "mailai_mailbox_uncertain_exploration_current",
            "Current exploration uncertain records by mailbox from SQLite state.",
            uncertain_exploration_by_mailbox,
            "mailbox_id",
        )
    )
    lines.extend(
        _labelled_metrics(
            "mailai_mailbox_uncertain_error_current",
            "Current non-exploration uncertain records by mailbox from SQLite state.",
            uncertain_error_by_mailbox,
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
    feedback_summary = cast(dict[str, object], learning.get("feedback_summary", {}))
    feedback_records = int(feedback_summary.get("records", 0) or 0)
    feedback_wrong_total = int(feedback_summary.get("wrong_total", 0) or 0)
    feedback_wrong_rate = float(feedback_summary.get("wrong_rate", 0.0) or 0.0)
    outcome_bad_total = int(feedback_summary.get("outcome_bad_total", 0) or 0)
    outcome_bad_rate = float(feedback_summary.get("outcome_bad_rate", 0.0) or 0.0)
    lines.extend(
        [
            "# HELP mailai_quality_feedback_records Total operator feedback records used by quality-learning.",
            "# TYPE mailai_quality_feedback_records gauge",
            f"mailai_quality_feedback_records {feedback_records}",
            "# HELP mailai_quality_feedback_wrong_total Total wrong classifications in operator feedback.",
            "# TYPE mailai_quality_feedback_wrong_total gauge",
            f"mailai_quality_feedback_wrong_total {feedback_wrong_total}",
            "# HELP mailai_quality_feedback_wrong_rate Wrong-classification share in operator feedback.",
            "# TYPE mailai_quality_feedback_wrong_rate gauge",
            f"mailai_quality_feedback_wrong_rate {feedback_wrong_rate}",
            "# HELP mailai_quality_feedback_outcome_bad_total Total outcome-bad labels in feedback.",
            "# TYPE mailai_quality_feedback_outcome_bad_total gauge",
            f"mailai_quality_feedback_outcome_bad_total {outcome_bad_total}",
            "# HELP mailai_quality_feedback_outcome_bad_rate Outcome-bad share over labeled outcomes.",
            "# TYPE mailai_quality_feedback_outcome_bad_rate gauge",
            f"mailai_quality_feedback_outcome_bad_rate {outcome_bad_rate}",
        ]
    )
    wrong_by_kind = cast(dict[str, int], feedback_summary.get("wrong_by_kind", {}))
    corrected_total = feedback_wrong_total
    correction_share = (corrected_total / feedback_records) if feedback_records else 0.0
    recent_feedback = _recent_feedback_summary(Path("logs/quality-feedback.jsonl"), window_hours=24)
    correction_time = _feedback_correction_time_summary(Path("logs/quality-feedback.jsonl"))
    feedback_7d = _feedback_window_summary(Path("logs/quality-feedback.jsonl"), window_days=7)
    llm_routed_total = float(summary["llm_routed"])
    llm_routed_7d = float(quality_7d.get("summary", {}).get("llm_routed", 0.0) or 0.0)
    corrections_per_1000 = (feedback_7d["corrected"] / llm_routed_7d * 1000.0) if llm_routed_7d > 0 else 0.0
    lines.extend(
        _labelled_metrics(
            "mailai_quality_feedback_wrong_by_kind",
            "Wrong classifications in operator feedback by learning kind.",
            wrong_by_kind,
            "kind",
        )
    )
    wrong_rate_by_kind = cast(dict[str, float], feedback_summary.get("wrong_rate_by_kind", {}))
    lines.extend(_labelled_metrics_float(
        "mailai_quality_feedback_wrong_rate_by_kind",
        "Wrong-classification share in operator feedback by learning kind.",
        wrong_rate_by_kind,
        "kind",
    ))
    lines.extend(
        [
            "# HELP mailai_quality_feedback_corrected_total Total corrected (was_wrong=true) feedback records.",
            "# TYPE mailai_quality_feedback_corrected_total gauge",
            f"mailai_quality_feedback_corrected_total {feedback_wrong_total}",
            "# HELP mailai_quality_feedback_correction_share Share of corrected feedback records over total feedback.",
            "# TYPE mailai_quality_feedback_correction_share gauge",
            f"mailai_quality_feedback_correction_share {correction_share}",
            "# HELP mailai_quality_feedback_recent_records_24h Feedback records appended in last 24 hours.",
            "# TYPE mailai_quality_feedback_recent_records_24h gauge",
            f"mailai_quality_feedback_recent_records_24h {recent_feedback['records']}",
            "# HELP mailai_quality_feedback_recent_corrected_24h Corrected feedback records (was_wrong=true) in last 24 hours.",
            "# TYPE mailai_quality_feedback_recent_corrected_24h gauge",
            f"mailai_quality_feedback_recent_corrected_24h {recent_feedback['corrected']}",
            "# HELP mailai_quality_feedback_time_to_correction_hours_avg Average time from prediction to correction (hours).",
            "# TYPE mailai_quality_feedback_time_to_correction_hours_avg gauge",
            f"mailai_quality_feedback_time_to_correction_hours_avg {correction_time['avg_hours']}",
            "# HELP mailai_quality_feedback_time_to_correction_hours_p50 Median time from prediction to correction (hours).",
            "# TYPE mailai_quality_feedback_time_to_correction_hours_p50 gauge",
            f"mailai_quality_feedback_time_to_correction_hours_p50 {correction_time['p50_hours']}",
            "# HELP mailai_quality_feedback_corrections_per_1000_llm_routes_7d Corrected feedback records per 1000 LLM-routed events (7d window).",
            "# TYPE mailai_quality_feedback_corrections_per_1000_llm_routes_7d gauge",
            f"mailai_quality_feedback_corrections_per_1000_llm_routes_7d {corrections_per_1000}",
            "# HELP mailai_quality_feedback_corrected_total_7d Corrected feedback records in 7d window.",
            "# TYPE mailai_quality_feedback_corrected_total_7d gauge",
            f"mailai_quality_feedback_corrected_total_7d {feedback_7d['corrected']}",
            "# HELP mailai_quality_feedback_outcome_bad_rate_7d Outcome-bad share in 7d window.",
            "# TYPE mailai_quality_feedback_outcome_bad_rate_7d gauge",
            f"mailai_quality_feedback_outcome_bad_rate_7d {feedback_7d['outcome_bad_rate']}",
        ]
    )
    exploration_total = 0
    prior_total = 0
    for action, count in action_counts.items():
        if "route_uncertain_exploration" in str(action):
            exploration_total += int(count)
        if "route_from_prior" in str(action):
            prior_total += int(count)
    exploration_base_total = llm_routed_total + prior_total + exploration_total
    exploration_share = (exploration_total / exploration_base_total) if exploration_base_total > 0 else 0.0
    lines.extend(
        [
            "# HELP mailai_learning_exploration_total Exploration-routed events total.",
            "# TYPE mailai_learning_exploration_total gauge",
            f"mailai_learning_exploration_total {exploration_total}",
            "# HELP mailai_learning_exploration_share Share of exploration routes over AI-routed events (llm+prior+exploration).",
            "# TYPE mailai_learning_exploration_share gauge",
            f"mailai_learning_exploration_share {exploration_share}",
            "# HELP mailai_learning_sender_prior_total Sender-prior routed events total.",
            "# TYPE mailai_learning_sender_prior_total gauge",
            f"mailai_learning_sender_prior_total {prior_total}",
        ]
    )
    tuner_14d = _exploration_tuner_window_summary(Path("logs/exploration-tuner.jsonl"), window_days=14)
    lines.extend(
        [
            "# HELP mailai_exploration_tuner_changes_14d Number of exploration-rate changes in tuner decisions over 14d.",
            "# TYPE mailai_exploration_tuner_changes_14d gauge",
            f"mailai_exploration_tuner_changes_14d {tuner_14d['changes']}",
            "# HELP mailai_exploration_tuner_runs_14d Number of tuner runs observed over 14d.",
            "# TYPE mailai_exploration_tuner_runs_14d gauge",
            f"mailai_exploration_tuner_runs_14d {tuner_14d['runs']}",
            "# HELP mailai_exploration_tuner_applied_changes_14d Number of applied exploration-rate changes over 14d.",
            "# TYPE mailai_exploration_tuner_applied_changes_14d gauge",
            f"mailai_exploration_tuner_applied_changes_14d {tuner_14d['applied_changes']}",
        ]
    )
    impact_scores: list[float] = []
    for proposal in cast(list[object], learning.get("proposals", [])):
        if not isinstance(proposal, dict):
            continue
        raw = proposal.get("impact_score")
        if raw is None:
            continue
        try:
            impact_scores.append(float(raw))
        except Exception:
            continue
    impact_avg = (sum(impact_scores) / len(impact_scores)) if impact_scores else 0.0
    impact_max = max(impact_scores) if impact_scores else 0.0
    lines.extend(
        [
            "# HELP mailai_quality_learning_impact_score_avg Average impact score across current quality-learning proposals.",
            "# TYPE mailai_quality_learning_impact_score_avg gauge",
            f"mailai_quality_learning_impact_score_avg {impact_avg}",
            "# HELP mailai_quality_learning_impact_score_max Maximum impact score across current quality-learning proposals.",
            "# TYPE mailai_quality_learning_impact_score_max gauge",
            f"mailai_quality_learning_impact_score_max {impact_max}",
        ]
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
    learner_effective_mode = ""
    learner_requested_mode = ""
    learner_blockers = 0
    challenger_promote = 0
    challenger_max_impact = 0.0
    challenger_policy_delta = 0
    challenger_reasons_count = 0
    if learner_summary:
        effective_mode = learner_summary.get("effective_mode")
        requested_mode = learner_summary.get("requested_mode")
        blockers_value = learner_summary.get("blockers_count")
        promote_value = learner_summary.get("champion_promote")
        max_impact_value = learner_summary.get("champion_max_impact_score")
        policy_delta_value = learner_summary.get("champion_policy_delta")
        reasons_count_value = learner_summary.get("champion_reasons_count")
        if isinstance(effective_mode, str):
            learner_effective_mode = effective_mode
        if isinstance(requested_mode, str):
            learner_requested_mode = requested_mode
        if isinstance(blockers_value, (int, float)):
            learner_blockers = int(blockers_value)
        if isinstance(promote_value, bool):
            challenger_promote = 1 if promote_value else 0
        if isinstance(max_impact_value, (int, float)):
            challenger_max_impact = float(max_impact_value)
        if isinstance(policy_delta_value, (int, float)):
            challenger_policy_delta = int(policy_delta_value)
        if isinstance(reasons_count_value, (int, float)):
            challenger_reasons_count = int(reasons_count_value)
    lines.extend(_learner_mode_series("mailai_autotune_learner_effective_mode", "Latest weekly autotune learner effective mode (binary by mode).", learner_effective_mode))
    lines.extend(_learner_mode_series("mailai_autotune_learner_requested_mode", "Latest weekly autotune learner requested mode (binary by mode).", learner_requested_mode))
    lines.extend(
        [
            "# HELP mailai_autotune_learner_blockers_count Latest weekly autotune learner blocker count.",
            "# TYPE mailai_autotune_learner_blockers_count gauge",
            f"mailai_autotune_learner_blockers_count {learner_blockers}",
            "# HELP mailai_autotune_champion_challenger_promote 1 when challenger recommends promote.",
            "# TYPE mailai_autotune_champion_challenger_promote gauge",
            f"mailai_autotune_champion_challenger_promote {challenger_promote}",
            "# HELP mailai_autotune_champion_challenger_max_impact_score Latest challenger max impact score.",
            "# TYPE mailai_autotune_champion_challenger_max_impact_score gauge",
            f"mailai_autotune_champion_challenger_max_impact_score {challenger_max_impact}",
            "# HELP mailai_autotune_champion_challenger_policy_delta Latest challenger estimated policy delta count.",
            "# TYPE mailai_autotune_champion_challenger_policy_delta gauge",
            f"mailai_autotune_champion_challenger_policy_delta {challenger_policy_delta}",
            "# HELP mailai_autotune_champion_challenger_reasons_count Latest challenger reasons count.",
            "# TYPE mailai_autotune_champion_challenger_reasons_count gauge",
            f"mailai_autotune_champion_challenger_reasons_count {challenger_reasons_count}",
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
    uncertain_error = int(cast(int, state.get("uncertain_error", state.get("uncertain", 0))))
    if uncertain_error > 0 or issues:
        return 1
    return 0


def _recent_feedback_summary(feedback_log: Path, *, window_hours: int) -> dict[str, int]:
    if not feedback_log.exists():
        return {"records": 0, "corrected": 0}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    records = 0
    corrected = 0
    try:
        lines = feedback_log.read_text(encoding="utf-8").splitlines()[-5000:]
    except Exception:
        return {"records": 0, "corrected": 0}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        raw = item.get("timestamp")
        if raw is None:
            continue
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        records += 1
        if bool(item.get("was_wrong", False)):
            corrected += 1
    return {"records": records, "corrected": corrected}


def _feedback_window_summary(feedback_log: Path, *, window_days: int) -> dict[str, float]:
    if not feedback_log.exists():
        return {"records": 0.0, "corrected": 0.0, "outcome_bad_rate": 0.0}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    records = 0
    corrected = 0
    outcome_total = 0
    outcome_bad = 0
    try:
        lines = feedback_log.read_text(encoding="utf-8").splitlines()[-10000:]
    except Exception:
        return {"records": 0.0, "corrected": 0.0, "outcome_bad_rate": 0.0}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        raw = item.get("timestamp")
        if raw is None:
            continue
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        records += 1
        if bool(item.get("was_wrong", False)):
            corrected += 1
        if "outcome_ok" in item:
            outcome_total += 1
            if not bool(item.get("outcome_ok")):
                outcome_bad += 1
    outcome_bad_rate = (outcome_bad / outcome_total) if outcome_total else 0.0
    return {"records": float(records), "corrected": float(corrected), "outcome_bad_rate": float(outcome_bad_rate)}


def _feedback_correction_time_summary(feedback_log: Path) -> dict[str, float]:
    if not feedback_log.exists():
        return {"avg_hours": 0.0, "p50_hours": 0.0}
    values: list[float] = []
    try:
        lines = feedback_log.read_text(encoding="utf-8").splitlines()[-5000:]
    except Exception:
        return {"avg_hours": 0.0, "p50_hours": 0.0}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if not isinstance(item, dict) or not bool(item.get("was_wrong", False)):
            continue
        raw_corr = item.get("timestamp")
        raw_pred = item.get("prediction_timestamp")
        if raw_corr is None or raw_pred is None:
            continue
        try:
            corr = datetime.fromisoformat(str(raw_corr).replace("Z", "+00:00"))
            pred = datetime.fromisoformat(str(raw_pred).replace("Z", "+00:00"))
        except ValueError:
            continue
        if corr.tzinfo is None:
            corr = corr.replace(tzinfo=timezone.utc)
        if pred.tzinfo is None:
            pred = pred.replace(tzinfo=timezone.utc)
        delta_h = (corr - pred).total_seconds() / 3600.0
        if delta_h < 0:
            continue
        values.append(delta_h)
    if not values:
        return {"avg_hours": 0.0, "p50_hours": 0.0}
    values.sort()
    mid = len(values) // 2
    p50 = values[mid] if len(values) % 2 == 1 else (values[mid - 1] + values[mid]) / 2.0
    avg = sum(values) / len(values)
    return {"avg_hours": round(avg, 4), "p50_hours": round(p50, 4)}


def _exploration_tuner_window_summary(log_path: Path, *, window_days: int) -> dict[str, float]:
    if not log_path.exists():
        return {"runs": 0.0, "changes": 0.0, "applied_changes": 0.0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    runs = 0
    changes = 0
    applied_changes = 0
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()[-5000:]
    except Exception:
        return {"runs": 0.0, "changes": 0.0, "applied_changes": 0.0}
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        ts_raw = item.get("generated_at")
        if ts_raw is None:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        runs += 1
        if bool(item.get("changed", False)):
            changes += 1
        if bool(item.get("applied", False)):
            applied_changes += 1
    return {"runs": float(runs), "changes": float(changes), "applied_changes": float(applied_changes)}


def _window_quality_metrics(quality_payload: dict[str, object], *, window_days: int) -> list[str]:
    summary = cast(dict[str, object], quality_payload.get("summary", {}))
    by_action = cast(dict[str, int], quality_payload.get("by_action", {}))
    records = float(summary.get("records", 0) or 0)
    uncertain = float(summary.get("uncertain", 0) or 0)
    failed = float(summary.get("failed", 0) or 0)
    cleanup_pending = float(summary.get("cleanup_pending", 0) or 0)
    llm_share = float(summary.get("llm_share", 0) or 0)
    rule_share = float(summary.get("rule_share", 0) or 0)
    processing_failures = float(sum(int(by_action.get(key, 0)) for key in _PROCESSING_FAILURE_ACTIONS))
    processing_failure_rate = (processing_failures / records) if records > 0 else 0.0
    return [
        f"# HELP mailai_quality_window_records_{window_days}d Audit-derived record count in rolling {window_days}d window.",
        f"# TYPE mailai_quality_window_records_{window_days}d gauge",
        f"mailai_quality_window_records_{window_days}d {records}",
        f"# HELP mailai_quality_window_uncertain_{window_days}d Audit-derived uncertain count in rolling {window_days}d window.",
        f"# TYPE mailai_quality_window_uncertain_{window_days}d gauge",
        f"mailai_quality_window_uncertain_{window_days}d {uncertain}",
        f"# HELP mailai_quality_window_failed_{window_days}d Audit-derived failed count in rolling {window_days}d window.",
        f"# TYPE mailai_quality_window_failed_{window_days}d gauge",
        f"mailai_quality_window_failed_{window_days}d {failed}",
        f"# HELP mailai_quality_window_cleanup_pending_{window_days}d Audit-derived cleanup_pending count in rolling {window_days}d window.",
        f"# TYPE mailai_quality_window_cleanup_pending_{window_days}d gauge",
        f"mailai_quality_window_cleanup_pending_{window_days}d {cleanup_pending}",
        f"# HELP mailai_quality_window_llm_share_{window_days}d LLM routing share in rolling {window_days}d window.",
        f"# TYPE mailai_quality_window_llm_share_{window_days}d gauge",
        f"mailai_quality_window_llm_share_{window_days}d {llm_share}",
        f"# HELP mailai_quality_window_rule_share_{window_days}d Rule routing share in rolling {window_days}d window.",
        f"# TYPE mailai_quality_window_rule_share_{window_days}d gauge",
        f"mailai_quality_window_rule_share_{window_days}d {rule_share}",
        f"# HELP mailai_quality_window_processing_failures_{window_days}d Processing failures in rolling {window_days}d window.",
        f"# TYPE mailai_quality_window_processing_failures_{window_days}d gauge",
        f"mailai_quality_window_processing_failures_{window_days}d {processing_failures}",
        f"# HELP mailai_quality_window_processing_failure_rate_{window_days}d Processing failure rate in rolling {window_days}d window.",
        f"# TYPE mailai_quality_window_processing_failure_rate_{window_days}d gauge",
        f"mailai_quality_window_processing_failure_rate_{window_days}d {processing_failure_rate}",
    ]


def _quality_improvement_metrics(*, current_7d: dict[str, object], previous_7d: dict[str, object]) -> list[str]:
    current_summary = cast(dict[str, object], current_7d.get("summary", {}))
    previous_summary = cast(dict[str, object], previous_7d.get("summary", {}))
    current_actions = cast(dict[str, int], current_7d.get("by_action", {}))
    previous_actions = cast(dict[str, int], previous_7d.get("by_action", {}))

    current_records = float(current_summary.get("records", 0) or 0)
    previous_records = float(previous_summary.get("records", 0) or 0)
    current_uncertain = float(current_summary.get("uncertain", 0) or 0)
    previous_uncertain = float(previous_summary.get("uncertain", 0) or 0)
    current_failures = float(sum(int(current_actions.get(key, 0)) for key in _PROCESSING_FAILURE_ACTIONS))
    previous_failures = float(sum(int(previous_actions.get(key, 0)) for key in _PROCESSING_FAILURE_ACTIONS))

    current_failure_rate = (current_failures / current_records) if current_records > 0 else 0.0
    previous_failure_rate = (previous_failures / previous_records) if previous_records > 0 else 0.0
    current_uncertain_rate = (current_uncertain / current_records) if current_records > 0 else 0.0
    previous_uncertain_rate = (previous_uncertain / previous_records) if previous_records > 0 else 0.0

    failure_rate_delta = previous_failure_rate - current_failure_rate
    uncertain_rate_delta = previous_uncertain_rate - current_uncertain_rate
    score = (0.7 * failure_rate_delta) + (0.3 * uncertain_rate_delta)
    score = max(-1.0, min(1.0, score))

    return [
        "# HELP mailai_quality_improvement_score_7d Weighted weekly quality improvement score (+ better, - worse).",
        "# TYPE mailai_quality_improvement_score_7d gauge",
        f"mailai_quality_improvement_score_7d {score}",
        "# HELP mailai_quality_improvement_processing_failure_delta_7d Previous-vs-current 7d processing failure rate delta (+ better).",
        "# TYPE mailai_quality_improvement_processing_failure_delta_7d gauge",
        f"mailai_quality_improvement_processing_failure_delta_7d {failure_rate_delta}",
        "# HELP mailai_quality_improvement_uncertain_delta_7d Previous-vs-current 7d uncertain rate delta (+ better).",
        "# TYPE mailai_quality_improvement_uncertain_delta_7d gauge",
        f"mailai_quality_improvement_uncertain_delta_7d {uncertain_rate_delta}",
        "# HELP mailai_quality_improvement_comparison_records_current_7d Records count in current 7d comparison window.",
        "# TYPE mailai_quality_improvement_comparison_records_current_7d gauge",
        f"mailai_quality_improvement_comparison_records_current_7d {current_records}",
        "# HELP mailai_quality_improvement_comparison_records_previous_7d Records count in previous 7d comparison window.",
        "# TYPE mailai_quality_improvement_comparison_records_previous_7d gauge",
        f"mailai_quality_improvement_comparison_records_previous_7d {previous_records}",
    ]


def _labelled_metrics(metric_name: str, help_text: str, mapping: dict[str, int], label_name: str) -> list[str]:
    escaped_help = help_text.replace("\\", "\\\\").replace("\n", "\\n")
    lines = [f"# HELP {metric_name} {escaped_help}", f"# TYPE {metric_name} gauge"]
    for key, value in sorted(mapping.items()):
        escaped_key = str(key).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        lines.append(f'{metric_name}{{{label_name}="{escaped_key}"}} {int(value)}')
    return lines


def _labelled_metrics_float(metric_name: str, help_text: str, mapping: dict[str, float], label_name: str) -> list[str]:
    escaped_help = help_text.replace("\\", "\\\\").replace("\n", "\\n")
    lines = [f"# HELP {metric_name} {escaped_help}", f"# TYPE {metric_name} gauge"]
    for key, value in sorted(mapping.items()):
        escaped_key = str(key).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        lines.append(f'{metric_name}{{{label_name}="{escaped_key}"}} {float(value)}')
    return lines


def _learner_mode_series(metric_name: str, help_text: str, active_mode: str) -> list[str]:
    modes = ("shadow", "propose", "autopromote")
    lines = [f"# HELP {metric_name} {help_text}", f"# TYPE {metric_name} gauge"]
    for mode in modes:
        value = 1 if active_mode == mode else 0
        lines.append(f'{metric_name}{{mode="{mode}"}} {value}')
    return lines


def _configured_mailbox_ids(env_file: Path | None) -> set[str]:
    if env_file is None:
        return set()
    path = Path(env_file)
    env_map = _load_env_file(path)
    mailbox_ids: set[str] = set()
    manifest_raw = env_map.get("MAILBOXES_CONFIG_PATH", "").strip()
    if manifest_raw:
        manifest_path = Path(manifest_raw)
        if not manifest_path.is_absolute():
            manifest_path = path.parent / manifest_path
        mailbox_ids.update(_mailbox_ids_from_manifest(manifest_path))
    if not mailbox_ids:
        imap_user = env_map.get("IMAP_USER", "").strip()
        if imap_user:
            mailbox_ids.add(_normalize_mailbox_id_from_user(imap_user))
    return {mailbox_id for mailbox_id in mailbox_ids if mailbox_id}


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _mailbox_ids_from_manifest(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    raw_mailboxes: list[object]
    if isinstance(payload, dict):
        candidate = payload.get("mailboxes", [])
        raw_mailboxes = candidate if isinstance(candidate, list) else []
    elif isinstance(payload, list):
        raw_mailboxes = payload
    else:
        raw_mailboxes = []

    mailbox_ids: set[str] = set()
    for item in raw_mailboxes:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("mailbox_id", "")).strip()
        if raw_id:
            mailbox_ids.add(raw_id)
            continue
        raw_user = str(item.get("imap_user", "")).strip()
        if raw_user:
            mailbox_ids.add(_normalize_mailbox_id_from_user(raw_user))
    return mailbox_ids


def _normalize_mailbox_id_from_user(imap_user: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", imap_user.strip().lower()).strip("_")
    return normalized or "mailbox_default"


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


def _latest_autotune_learner_summary(log_dir: Path = Path("logs/weekly-autotune")) -> dict[str, object] | None:
    files = sorted(log_dir.glob("weekly-autotune-*.quality.json"), reverse=True)
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        learner = payload.get("learner")
        if not isinstance(learner, dict):
            continue
        blockers = learner.get("autopromote_blockers", [])
        blockers_count = 0
        if isinstance(blockers, list):
            blockers_count = len(blockers)
        champion = learner.get("champion_challenger", {})
        champion_promote: bool | None = None
        champion_max_impact_score = 0.0
        champion_policy_delta = 0
        champion_reasons_count = 0
        if isinstance(champion, dict):
            promote_value = champion.get("promote")
            if isinstance(promote_value, bool):
                champion_promote = promote_value
            max_impact_value = champion.get("max_impact_score")
            if isinstance(max_impact_value, (int, float)):
                champion_max_impact_score = float(max_impact_value)
            policy_delta_value = champion.get("policy_delta")
            if isinstance(policy_delta_value, (int, float)):
                champion_policy_delta = int(policy_delta_value)
            reasons_value = champion.get("reasons")
            if isinstance(reasons_value, list):
                champion_reasons_count = len(reasons_value)
        return {
            "effective_mode": learner.get("effective_mode"),
            "requested_mode": learner.get("requested_mode"),
            "blockers_count": blockers_count,
            "champion_promote": champion_promote,
            "champion_max_impact_score": champion_max_impact_score,
            "champion_policy_delta": champion_policy_delta,
            "champion_reasons_count": champion_reasons_count,
        }
    return None


def serve_metrics(
    *,
    host: str,
    port: int,
    payload_builder: Callable[[], str],
    trigger_token: str | None = None,
    trigger_link_token: str | None = None,
    trigger_label: str = "com.mailai.autotune.weekly.prod",
    trigger_allowlist: set[str] | None = None,
    trigger_link_origin_allowlist: set[str] | None = None,
    trigger_cooldown_seconds: int = 60,
    trigger_link_unauthorized_cooldown_seconds: int = 15,
    trigger_audit_log: Path | None = None,
    trigger_audit_max_bytes: int = 2_000_000,
    trigger_audit_backup_count: int = 5,
    payload_refresh_seconds: int = 300,
) -> None:
    allowlist = trigger_allowlist or {"127.0.0.1", "::1"}
    origin_allowlist = trigger_link_origin_allowlist or {
        "http://127.0.0.1:3005",
        "http://localhost:3005",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:9177",
        "http://localhost:9177",
    }
    trigger_lock = threading.Lock()
    trigger_audit_lock = threading.Lock()
    last_trigger_at_link: datetime | None = None
    last_trigger_at_token: datetime | None = None
    trigger_in_flight_link = False
    trigger_in_flight_token = False
    unauthorized_backoff_by_ip: dict[str, datetime] = {}
    trigger_stats: dict[str, int] = {
        "token_ok": 0,
        "token_unauthorized": 0,
        "token_forbidden_ip": 0,
        "token_disabled": 0,
        "token_cooldown": 0,
        "token_launch_error": 0,
        "link_ok": 0,
        "link_launch_error": 0,
        "link_forbidden_origin": 0,
        "link_forbidden_ip": 0,
        "link_unauthorized": 0,
        "link_unauthorized_cooldown": 0,
        "link_method_not_allowed": 0,
        "link_cooldown": 0,
        "metrics_served": 0,
    }
    audit_log = trigger_audit_log or Path("logs/autotune-trigger-audit.jsonl")
    refresh_seconds = max(0, int(payload_refresh_seconds))

    payload_cache_lock = threading.Lock()
    payload_refresh_lock = threading.Lock()
    payload_cache: dict[str, object] = {
        "payload": None,
        "generated_at": None,
        "last_error": "",
        "last_error_at": None,
        "last_duration_seconds": 0.0,
        "refresh_success_total": 0,
        "refresh_failure_total": 0,
    }
    refresh_stop = threading.Event()
    refresh_thread: threading.Thread | None = None

    def _append_trigger_audit(*, endpoint: str, status: str, client_ip: str, reason: str) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint,
            "status": status,
            "client_ip": client_ip,
            "reason": reason,
        }
        try:
            with trigger_audit_lock:
                if trigger_audit_max_bytes > 0:
                    rotate_audit_log(
                        audit_log,
                        max_bytes=trigger_audit_max_bytes,
                        backup_count=max(1, int(trigger_audit_backup_count)),
                    )
                audit_log.parent.mkdir(parents=True, exist_ok=True)
                with audit_log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            LOGGER.exception("failed to append trigger audit")

    def _refresh_metrics_payload(*, reason: str) -> bool:
        # Protect the builder from concurrent scrapes to avoid CPU thrash on large audit logs.
        with payload_refresh_lock:
            started = time.monotonic()
            try:
                payload = payload_builder()
            except Exception as exc:
                duration = time.monotonic() - started
                with payload_cache_lock:
                    payload_cache["last_error"] = str(exc) or exc.__class__.__name__
                    payload_cache["last_error_at"] = datetime.now(timezone.utc)
                    payload_cache["last_duration_seconds"] = duration
                    payload_cache["refresh_failure_total"] = int(payload_cache["refresh_failure_total"]) + 1
                LOGGER.exception("metrics payload generation failed (%s)", reason)
                return False

            duration = time.monotonic() - started
            with payload_cache_lock:
                payload_cache["payload"] = payload
                payload_cache["generated_at"] = datetime.now(timezone.utc)
                payload_cache["last_error"] = ""
                payload_cache["last_error_at"] = None
                payload_cache["last_duration_seconds"] = duration
                payload_cache["refresh_success_total"] = int(payload_cache["refresh_success_total"]) + 1
            return True

    def _run_trigger(
        *,
        endpoint: str,
        stat_ok: str,
        stat_cooldown: str,
        stat_error: str,
        client_ip: str,
    ) -> tuple[int, dict[str, object]]:
        nonlocal last_trigger_at_link, last_trigger_at_token, trigger_in_flight_link, trigger_in_flight_token
        now = datetime.now(timezone.utc)
        cooldown_hit = False
        retry_after = 0
        with trigger_lock:
            in_flight = trigger_in_flight_link if endpoint == "link" else trigger_in_flight_token
            if in_flight:
                trigger_stats[stat_cooldown] = trigger_stats.get(stat_cooldown, 0) + 1
                cooldown_hit = True
                retry_after = max(1, int(trigger_cooldown_seconds))
            if cooldown_hit:
                pass
            last_trigger_at = last_trigger_at_link if endpoint == "link" else last_trigger_at_token
            if not cooldown_hit and last_trigger_at is not None:
                elapsed = (now - last_trigger_at).total_seconds()
                if elapsed < float(trigger_cooldown_seconds):
                    trigger_stats[stat_cooldown] = trigger_stats.get(stat_cooldown, 0) + 1
                    cooldown_hit = True
                    retry_after = int(trigger_cooldown_seconds - elapsed)
            if not cooldown_hit:
                if endpoint == "link":
                    trigger_in_flight_link = True
                else:
                    trigger_in_flight_token = True
        if cooldown_hit:
            _append_trigger_audit(endpoint=endpoint, status="cooldown", client_ip=client_ip, reason="cooldown")
            return 429, {"ok": False, "error": "cooldown", "retry_after_seconds": retry_after}
        try:
            subprocess.run(
                ["/bin/launchctl", "kickstart", "-k", f"gui/{_uid()}/{trigger_label}"],
                check=True,
                capture_output=True,
                text=True,
            )
            with trigger_lock:
                if endpoint == "link":
                    last_trigger_at_link = now
                    trigger_in_flight_link = False
                else:
                    last_trigger_at_token = now
                    trigger_in_flight_token = False
                trigger_stats[stat_ok] = trigger_stats.get(stat_ok, 0) + 1
            _append_trigger_audit(endpoint=endpoint, status="ok", client_ip=client_ip, reason="triggered")
            return 200, {"ok": True, "triggered": trigger_label}
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            with trigger_lock:
                if endpoint == "link":
                    trigger_in_flight_link = False
                else:
                    trigger_in_flight_token = False
                trigger_stats[stat_error] = trigger_stats.get(stat_error, 0) + 1
            _append_trigger_audit(endpoint=endpoint, status="launch_error", client_ip=client_ip, reason=stderr or "launch_error")
            return 500, {"ok": False, "error": "trigger failed", "details": stderr}

    def _handle_trigger(*, headers: object, client_ip: str) -> tuple[int, dict[str, object]]:
        if client_ip and client_ip not in allowlist:
            with trigger_lock:
                trigger_stats["token_forbidden_ip"] += 1
            _append_trigger_audit(endpoint="token", status="forbidden_ip", client_ip=client_ip, reason="forbidden_ip")
            return 403, {"ok": False, "error": "forbidden ip"}
        if not trigger_token:
            with trigger_lock:
                trigger_stats["token_disabled"] += 1
            _append_trigger_audit(endpoint="token", status="disabled", client_ip=client_ip, reason="missing_token")
            return 403, {"ok": False, "error": "trigger disabled: missing token"}
        auth = ""
        if hasattr(headers, "get"):
            auth = str(headers.get("X-Trigger-Token", "") or "")
        if not hmac.compare_digest(auth, trigger_token):
            with trigger_lock:
                trigger_stats["token_unauthorized"] += 1
            _append_trigger_audit(endpoint="token", status="unauthorized", client_ip=client_ip, reason="unauthorized")
            return 401, {"ok": False, "error": "unauthorized"}
        return _run_trigger(
            endpoint="token",
            stat_ok="token_ok",
            stat_cooldown="token_cooldown",
            stat_error="token_launch_error",
            client_ip=client_ip,
        )

    def _handle_trigger_link(*, headers: object, client_ip: str, form_params: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
        if client_ip and client_ip not in allowlist:
            with trigger_lock:
                trigger_stats["link_forbidden_ip"] += 1
            _append_trigger_audit(endpoint="link", status="forbidden_ip", client_ip=client_ip, reason="forbidden_ip")
            return 403, {"ok": False, "error": "forbidden ip"}
        if trigger_link_token:
            now = datetime.now(timezone.utc)
            with trigger_lock:
                blocked_until = unauthorized_backoff_by_ip.get(client_ip or "")
                if blocked_until is not None and now < blocked_until:
                    trigger_stats["link_unauthorized_cooldown"] += 1
                    retry_after = max(1, int((blocked_until - now).total_seconds()))
                    _append_trigger_audit(endpoint="link", status="unauthorized_cooldown", client_ip=client_ip, reason="unauthorized_cooldown")
                    return 429, {"ok": False, "error": "unauthorized cooldown", "retry_after_seconds": retry_after}
            provided = ""
            if form_params is not None:
                provided = str(form_params.get("token", "") or "")
            if not hmac.compare_digest(provided, trigger_link_token):
                with trigger_lock:
                    trigger_stats["link_unauthorized"] += 1
                    unauthorized_backoff_by_ip[client_ip or ""] = now + timedelta(seconds=max(1, int(trigger_link_unauthorized_cooldown_seconds)))
                _append_trigger_audit(endpoint="link", status="unauthorized", client_ip=client_ip, reason="unauthorized")
                return 401, {"ok": False, "error": "unauthorized"}
            with trigger_lock:
                unauthorized_backoff_by_ip.pop(client_ip or "", None)
        origin = ""
        referer = ""
        if hasattr(headers, "get"):
            origin = str(headers.get("Origin", "") or "").strip()
            referer = str(headers.get("Referer", "") or "").strip()
        normalized_origin_allowlist = {_normalized_origin(item) for item in origin_allowlist if _normalized_origin(item)}
        if _normalized_origin(origin) in normalized_origin_allowlist:
            return _run_trigger(
                endpoint="link",
                stat_ok="link_ok",
                stat_cooldown="link_cooldown",
                stat_error="link_launch_error",
                client_ip=client_ip,
            )
        if _normalized_origin(referer) in normalized_origin_allowlist:
            return _run_trigger(
                endpoint="link",
                stat_ok="link_ok",
                stat_cooldown="link_cooldown",
                stat_error="link_launch_error",
                client_ip=client_ip,
            )
        with trigger_lock:
            trigger_stats["link_forbidden_origin"] += 1
        _append_trigger_audit(endpoint="link", status="forbidden_origin", client_ip=client_ip, reason="forbidden_origin")
        return 403, {"ok": False, "error": "forbidden origin"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/autotune/trigger-ui":
                token_field = ""
                if trigger_link_token:
                    token_value = json.dumps(trigger_link_token, ensure_ascii=False)
                    token_field = f"<input type='hidden' name='token' value={token_value}>"
                body = (
                    "<!doctype html><html><body><form id='f' method='post' action='/autotune/trigger-link'>"
                    + token_field
                    + "</form><script>document.getElementById('f').submit();</script></body></html>\n"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/autotune/trigger-link":
                with trigger_lock:
                    trigger_stats["link_method_not_allowed"] += 1
                body = (json.dumps({"ok": False, "error": "method not allowed"}, ensure_ascii=False) + "\n").encode("utf-8")
                self.send_response(405)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Allow", "POST")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/autotune/trigger":
                body = (json.dumps({"ok": False, "error": "method not allowed"}, ensure_ascii=False) + "\n").encode("utf-8")
                self.send_response(405)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Allow", "POST")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path not in {"/metrics", "/metrics/"}:
                self.send_response(404)
                self.end_headers()
                return
            with payload_cache_lock:
                cached_payload = cast(str | None, payload_cache["payload"])
            if cached_payload is None and not _refresh_metrics_payload(reason="on_demand"):
                body = b"metrics payload generation failed\n"
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            with payload_cache_lock:
                cached_payload = cast(str | None, payload_cache["payload"])
                generated_at = cast(datetime | None, payload_cache["generated_at"])
                last_error = str(payload_cache["last_error"] or "")
                last_error_at = cast(datetime | None, payload_cache["last_error_at"])
                last_duration_seconds = float(payload_cache["last_duration_seconds"] or 0.0)
                refresh_success_total = int(payload_cache["refresh_success_total"] or 0)
                refresh_failure_total = int(payload_cache["refresh_failure_total"] or 0)

            if cached_payload is None:
                body = b"metrics payload generation failed\n"
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            with trigger_lock:
                trigger_stats["metrics_served"] += 1
                stats_snapshot = dict(trigger_stats)
            now = datetime.now(timezone.utc)
            cache_age_seconds = max(0.0, (now - generated_at).total_seconds()) if generated_at else -1.0
            last_success_unixtime = generated_at.timestamp() if generated_at else 0.0
            last_failure_unixtime = last_error_at.timestamp() if last_error_at else 0.0
            has_error = 1 if last_error else 0

            stats_lines = [
                "# HELP mailai_autotune_trigger_requests_total Trigger endpoint requests by result/status.",
                "# TYPE mailai_autotune_trigger_requests_total counter",
            ]
            for key, value in sorted(stats_snapshot.items()):
                stats_lines.append(f'mailai_autotune_trigger_requests_total{{status="{key}"}} {int(value)}')
            stats_lines.extend(
                [
                    "# HELP mailai_metrics_payload_cache_age_seconds Age of cached metrics payload in seconds.",
                    "# TYPE mailai_metrics_payload_cache_age_seconds gauge",
                    f"mailai_metrics_payload_cache_age_seconds {cache_age_seconds}",
                    "# HELP mailai_metrics_payload_refresh_last_duration_seconds Duration of last payload refresh.",
                    "# TYPE mailai_metrics_payload_refresh_last_duration_seconds gauge",
                    f"mailai_metrics_payload_refresh_last_duration_seconds {last_duration_seconds}",
                    "# HELP mailai_metrics_payload_refresh_success_total Successful payload refresh count.",
                    "# TYPE mailai_metrics_payload_refresh_success_total counter",
                    f"mailai_metrics_payload_refresh_success_total {refresh_success_total}",
                    "# HELP mailai_metrics_payload_refresh_failure_total Failed payload refresh count.",
                    "# TYPE mailai_metrics_payload_refresh_failure_total counter",
                    f"mailai_metrics_payload_refresh_failure_total {refresh_failure_total}",
                    "# HELP mailai_metrics_payload_refresh_last_success_unixtime Unix timestamp of last successful payload refresh.",
                    "# TYPE mailai_metrics_payload_refresh_last_success_unixtime gauge",
                    f"mailai_metrics_payload_refresh_last_success_unixtime {last_success_unixtime}",
                    "# HELP mailai_metrics_payload_refresh_last_failure_unixtime Unix timestamp of last failed payload refresh.",
                    "# TYPE mailai_metrics_payload_refresh_last_failure_unixtime gauge",
                    f"mailai_metrics_payload_refresh_last_failure_unixtime {last_failure_unixtime}",
                    "# HELP mailai_metrics_payload_refresh_last_error 1 when last refresh ended with error.",
                    "# TYPE mailai_metrics_payload_refresh_last_error gauge",
                    f"mailai_metrics_payload_refresh_last_error {has_error}",
                ]
            )
            body = (cached_payload.rstrip("\n") + "\n" + "\n".join(stats_lines) + "\n").encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/autotune/trigger-link":
                client_ip = str(self.client_address[0]) if getattr(self, "client_address", None) else ""
                form_params: dict[str, str] = {}
                try:
                    content_length = int(str(self.headers.get("Content-Length", "0") or "0")) if hasattr(self.headers, "get") else 0
                except Exception:
                    content_length = 0
                if content_length > 0:
                    raw = self.rfile.read(content_length) if hasattr(self, "rfile") else b""
                    parsed_form = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
                    form_params = {key: values[0] if values else "" for key, values in parsed_form.items()}
                status, payload = _handle_trigger_link(headers=self.headers, client_ip=client_ip, form_params=form_params)
                body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path != "/autotune/trigger":
                self.send_response(404)
                self.end_headers()
                return
            client_ip = str(self.client_address[0]) if getattr(self, "client_address", None) else ""
            status, payload = _handle_trigger(headers=self.headers, client_ip=client_ip)
            body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    def _refresh_startup_async() -> None:
        _refresh_metrics_payload(reason="startup_async")

    startup_refresh_thread = threading.Thread(
        target=_refresh_startup_async,
        name="mailai-metrics-refresh-startup",
        daemon=True,
    )
    startup_refresh_thread.start()

    if refresh_seconds > 0:
        def _refresh_loop() -> None:
            while not refresh_stop.wait(float(refresh_seconds)):
                _refresh_metrics_payload(reason="periodic")

        refresh_thread = threading.Thread(target=_refresh_loop, name="mailai-metrics-refresh", daemon=True)
        refresh_thread.start()

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    finally:
        refresh_stop.set()
        if refresh_thread is not None:
            refresh_thread.join(timeout=1.0)
        server.server_close()


def _uid() -> str:
    try:
        return str(int(subprocess.check_output(["/usr/bin/id", "-u"], text=True).strip()))
    except Exception:
        return "501"


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
    parser.add_argument("--oneshot", action="store_true", help="Print metrics once and exit")
    parser.add_argument("--trigger-token", default=None, help="Token required for /autotune/trigger")
    parser.add_argument("--trigger-link-token", default=None, help="Optional form token required for /autotune/trigger-link")
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
    parser.add_argument(
        "--trigger-cooldown-seconds",
        type=int,
        default=60,
        help="Cooldown between accepted trigger executions.",
    )
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

    builder: Callable[[], str] = lambda: build_metrics_payload(
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

    if args.oneshot:
        print(builder(), end="")
        return

    serve_metrics(
        host=args.host,
        port=args.port,
        payload_builder=builder,
        trigger_token=args.trigger_token,
        trigger_link_token=args.trigger_link_token,
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
