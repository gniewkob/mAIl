from __future__ import annotations

import argparse
import difflib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any

from .quality_report import build_quality_payload
from .reporting import summarize_state


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _top_items(mapping: dict[str, int], limit: int = 5) -> list[dict[str, int | str]]:
    return [
        {"key": key, "count": count}
        for key, count in sorted(mapping.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _load_uncertain_message_ids(state_db: Path, limit: int = 50) -> list[str]:
    if not state_db.exists():
        return []
    with sqlite3.connect(state_db) as conn:
        rows = conn.execute(
            """
            SELECT message_id
            FROM email_processing_state
            WHERE status = 'uncertain' AND message_id IS NOT NULL AND message_id != ''
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _extract_message_id_domains(message_ids: list[str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for message_id in message_ids:
        match = re.search(r"@([A-Za-z0-9._-]+\.[A-Za-z0-9._-]+)", message_id)
        if not match:
            continue
        domain = match.group(1).lower()
        counts[domain] += 1
    return dict(sorted(counts.items()))


def _rule_engine_patch_diff(candidate_domains: list[str]) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    rule_engine_path = repo_root / "src/mail_ai_agent/rule_engine.py"
    original = rule_engine_path.read_text(encoding="utf-8")

    escaped_domains = "|".join(domain.replace(".", r"\.") for domain in candidate_domains)
    updated = original.replace(
        'SYSTEM_PATTERNS = ("mailer-daemon", "delivery status notification", "failure notice", "postmaster")\n',
        'SYSTEM_PATTERNS = ("mailer-daemon", "delivery status notification", "failure notice", "postmaster")\n'
        "LOW_SIGNAL_MESSAGE_ID_REGEX = re.compile(\n"
        f'    r"@({escaped_domains})\\b",\n'
        "    flags=re.IGNORECASE,\n"
        ")\n",
    )
    updated = updated.replace(
        '    combined = " ".join([subject, sender, body])\n',
        '    message_id = (parsed_email.message_id or "").lower()\n'
        '    combined = " ".join([subject, sender, body, message_id])\n',
    )
    updated = updated.replace(
        "    billing_pat = _billing_email_pattern(getattr(mailbox, \"billing_payment_email\", None))\n",
        "    if LOW_SIGNAL_MESSAGE_ID_REGEX.search(message_id):\n"
        "        return RuleDecision(\n"
        '            category="other",\n'
        '            target_folder=category_to_folder("other", mailbox),\n'
        '            action="skip_ai",\n'
        '            reason="low-signal recurring message-id pattern matched after no stronger rule matched",\n'
        "        )\n\n"
        "    billing_pat = _billing_email_pattern(getattr(mailbox, \"billing_payment_email\", None))\n",
    )

    return "".join(
        difflib.unified_diff(
            original.splitlines(True),
            updated.splitlines(True),
            fromfile="a/src/mail_ai_agent/rule_engine.py",
            tofile="b/src/mail_ai_agent/rule_engine.py",
        )
    )


def _build_proposals(
    *,
    current_uncertain: int,
    current_failed: int,
    cleanup_pending: int,
    parse_errors_total: int,
    top_uncertain_mailboxes: list[dict[str, int | str]],
    uncertain_action_mix: dict[str, int],
) -> list[dict[str, str]]:
    proposals: list[dict[str, str]] = []

    uncertain_routes = int(uncertain_action_mix.get("move_route_uncertain", 0))
    if current_uncertain > 0 and top_uncertain_mailboxes:
        top_mailbox = str(top_uncertain_mailboxes[0]["key"])
        proposals.append(
            {
                "kind": "rule_engine",
                "priority": "medium",
                "title": "Review repeated uncertain patterns in the top mailbox",
                "suggested_change": (
                    f"Sample the current uncertain backlog in `{top_mailbox}` and promote repeatable patterns "
                    "into deterministic rules when they represent newsletters, outreach, or system mail."
                ),
            }
        )
    if uncertain_routes > 0:
        proposals.append(
            {
                "kind": "prompt",
                "priority": "medium",
                "title": "Add fresh uncertain examples to the LLM prompt set",
                "suggested_change": (
                    "Use the newest uncertain examples as few-shot prompt examples, especially when the model "
                    "still returns category `other` with low confidence."
                ),
            }
        )
    if parse_errors_total > 0:
        proposals.append(
            {
                "kind": "parser",
                "priority": "medium",
                "title": "Harden parser handling for recurring parse failures",
                "suggested_change": (
                    "Inspect recent parse_error history and add targeted charset or MIME normalization before "
                    "relaxing routing logic."
                ),
            }
        )
    if cleanup_pending > 0 or current_failed > 0:
        proposals.append(
            {
                "kind": "ops",
                "priority": "high",
                "title": "Resolve operational state before more quality tuning",
                "suggested_change": (
                    "Clear current failed or cleanup_pending state before applying further routing or prompt changes."
                ),
            }
        )
    if not proposals:
        proposals.append(
            {
                "kind": "ops",
                "priority": "low",
                "title": "No immediate quality changes proposed",
                "suggested_change": "Continue monitoring and wait for a larger uncertain or parse_error sample.",
            }
        )
    return proposals


def build_quality_learning_payload(
    *,
    state_db: Path,
    audit_log: Path,
    window_days: int | None = None,
) -> dict[str, Any]:
    state_summary = summarize_state(state_db)
    quality = build_quality_payload(audit_log, window_days=window_days)

    statuses = state_summary.get("statuses", {})
    uncertain_by_mailbox = state_summary.get("uncertain_by_mailbox", {})
    failed_by_mailbox = state_summary.get("failed_by_mailbox", {})
    cleanup_pending = int(state_summary.get("cleanup_pending", 0))
    current_uncertain = int(statuses.get("uncertain", 0))
    current_failed = int(statuses.get("failed", 0))
    parse_errors_total = int(quality["by_category"].get("parse_error", 0))
    uncertain_message_id_domains = _extract_message_id_domains(_load_uncertain_message_ids(state_db))
    recurring_message_id_domains = [
        domain for domain, count in uncertain_message_id_domains.items() if count >= 2
    ]

    recommendations: list[str] = []
    top_uncertain_mailboxes = _top_items(uncertain_by_mailbox)
    if current_uncertain > 0:
        if top_uncertain_mailboxes:
            recommendations.append(
                "Review the top uncertain mailbox first and promote repeatable patterns into rules or prompt examples."
            )
        recommendations.append(
            "Keep uncertain reserved for genuinely ambiguous mail and operational failures; route moderate-confidence other mail directly to INBOX.Other."
        )
    if parse_errors_total > 0:
        recommendations.append(
            "Inspect parse_error history and parser charset handling before relaxing routing logic."
        )
    if cleanup_pending > 0:
        recommendations.append(
            "Run cleanup_cli for the affected mailbox before changing routing or thresholds."
        )
    if current_failed > 0:
        recommendations.append(
            "Investigate current failed state before any quality tuning; runtime failures should be resolved first."
        )
    if not recommendations:
        recommendations.append("No active quality issues detected; continue monitoring uncertain and parse_error trends.")

    recent_uncertain = quality.get("recent_uncertain", [])
    uncertain_actions = Counter(
        str(item.get("action_taken"))
        for item in recent_uncertain
        if item.get("action_taken")
    )
    proposals = _build_proposals(
        current_uncertain=current_uncertain,
        current_failed=current_failed,
        cleanup_pending=cleanup_pending,
        parse_errors_total=parse_errors_total,
        top_uncertain_mailboxes=top_uncertain_mailboxes,
        uncertain_action_mix=dict(sorted(uncertain_actions.items())),
    )
    if recurring_message_id_domains:
        for proposal in proposals:
            if proposal["kind"] == "rule_engine":
                proposal["patch_target"] = "src/mail_ai_agent/rule_engine.py"
                proposal["patch_basis"] = ", ".join(recurring_message_id_domains)
                proposal["patch_diff"] = _rule_engine_patch_diff(recurring_message_id_domains)
                break
    proposal_counts = Counter(proposal["kind"] for proposal in proposals)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "state": {
            "records": int(state_summary.get("records", 0)),
            "processed": int(statuses.get("processed", 0)),
            "uncertain": current_uncertain,
            "failed": current_failed,
            "cleanup_pending": cleanup_pending,
        },
        "current_uncertain_by_mailbox": dict(sorted(uncertain_by_mailbox.items())),
        "current_failed_by_mailbox": dict(sorted(failed_by_mailbox.items())),
        "audit_summary": quality["summary"],
        "top_categories": _top_items(quality["by_category"]),
        "top_actions": _top_items(quality["by_action"]),
        "top_target_folders": _top_items(quality["by_target_folder"]),
        "top_uncertain_mailboxes": top_uncertain_mailboxes,
        "recent_uncertain_action_mix": dict(sorted(uncertain_actions.items())),
        "recent_uncertain": recent_uncertain[:10],
        "recent_failures": quality.get("recent_failures", [])[:10],
        "uncertain_message_id_domains": uncertain_message_id_domains,
        "proposals": proposals,
        "proposal_counts": dict(sorted(proposal_counts.items())),
        "recommendations": recommendations,
    }


def render_quality_learning_markdown(payload: dict[str, Any]) -> str:
    state = payload["state"]
    lines = [
        "# Quality Learning Report",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Processed: `{state['processed']}`",
        f"- Current uncertain: `{state['uncertain']}`",
        f"- Current failed: `{state['failed']}`",
        f"- Current cleanup_pending: `{state['cleanup_pending']}`",
        "",
        "## Top uncertain mailboxes",
    ]

    top_uncertain = payload["top_uncertain_mailboxes"]
    if top_uncertain:
        for row in top_uncertain:
            lines.append(f"- `{row['key']}`: `{row['count']}`")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Recent uncertain action mix",
        ]
    )
    action_mix = payload["recent_uncertain_action_mix"]
    if action_mix:
        for key, count in action_mix.items():
            lines.append(f"- `{key}`: `{count}`")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Proposed changes",
        ]
    )
    for item in payload["proposals"]:
        lines.append(
            f"- [{item['kind']}/{item['priority']}] {item['title']}: {item['suggested_change']}"
        )
        if item.get("patch_target"):
            lines.append(f"  patch target: `{item['patch_target']}`")
        if item.get("patch_path"):
            lines.append(f"  patch file: `{item['patch_path']}`")

    lines.extend(
        [
            "",
            "## Recommendations",
        ]
    )
    for item in payload["recommendations"]:
        lines.append(f"- {item}")

    return "\n".join(lines) + "\n"


def run_quality_learning(
    *,
    state_db: Path,
    audit_log: Path,
    output_dir: Path,
    window_days: int | None = None,
) -> dict[str, Any]:
    payload = build_quality_learning_payload(state_db=state_db, audit_log=audit_log, window_days=window_days)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = _utc_stamp()
    json_path = output_dir / f"quality-learning-{stamp}.json"
    md_path = output_dir / f"quality-learning-{stamp}.md"
    patch_paths: list[str] = []
    for index, proposal in enumerate(payload["proposals"], start=1):
        patch_diff = proposal.get("patch_diff")
        if not patch_diff:
            continue
        patch_path = output_dir / f"quality-learning-{stamp}-proposal-{index}.patch"
        patch_path.write_text(str(patch_diff), encoding="utf-8")
        proposal["patch_path"] = str(patch_path)
        patch_paths.append(str(patch_path))
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_quality_learning_markdown(payload), encoding="utf-8")
    return {
        "generated_at": payload["generated_at"],
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "patch_paths": patch_paths,
        "state": payload["state"],
        "recommendations": payload["recommendations"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a quality-learning report from current state and audit data.")
    parser.add_argument("--state-db", default="data/state.sqlite", help="Path to SQLite state DB")
    parser.add_argument("--audit-log", default="logs/audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--output-dir", default="logs/quality-learning", help="Directory for generated reports")
    parser.add_argument(
        "--window-days",
        type=int,
        default=None,
        help="Optional rolling window size for audit analysis. Example: 14",
    )
    args = parser.parse_args()

    payload = run_quality_learning(
        state_db=Path(args.state_db),
        audit_log=Path(args.audit_log),
        output_dir=Path(args.output_dir),
        window_days=args.window_days,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
