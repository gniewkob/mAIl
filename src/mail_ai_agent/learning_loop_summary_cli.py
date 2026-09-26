from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .quality_learning_cli import build_quality_learning_payload
from .quality_report import build_quality_payload, build_quality_payload_between


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_summary(*, state_db: Path, audit_log: Path, feedback_log: Path) -> dict[str, Any]:
    q7 = build_quality_payload(audit_log, window_days=7)
    q14 = build_quality_payload(audit_log, window_days=14)
    q_prev7 = build_quality_payload_between(audit_log, start_days_ago=14, end_days_ago=7)
    learning = build_quality_learning_payload(state_db=state_db, audit_log=audit_log, feedback_log=feedback_log)
    s7 = q7.get("summary", {})
    s14 = q14.get("summary", {})
    s_prev7 = q_prev7.get("summary", {})
    recs: list[str] = []
    uncertain7 = int(s7.get("uncertain", 0) or 0)
    uncertain14 = int(s14.get("uncertain", 0) or 0)
    uncertain_prev7 = int(s_prev7.get("uncertain", 0) or 0)
    failed7 = int(s7.get("failed", 0) or 0)
    failed14 = int(s14.get("failed", 0) or 0)
    failed_prev7 = int(s_prev7.get("failed", 0) or 0)
    feedback = learning.get("feedback_summary", {}) if isinstance(learning, dict) else {}
    wrong_rate = float(feedback.get("wrong_rate", 0.0) or 0.0)
    outcome_bad = float(feedback.get("outcome_bad_rate", 0.0) or 0.0)
    if uncertain7 > uncertain_prev7:
        recs.append("Uncertain trend worsened in current 7d vs previous 7d; increase exploration review throughput.")
    if failed7 > failed_prev7:
        recs.append("Failure trend worsened in current 7d vs previous 7d; prioritize pipeline/runtime remediation before threshold tuning.")
    if wrong_rate >= 0.15:
        recs.append("Feedback wrong-rate high; prioritize prompt/rule proposals with highest impact_score.")
    if outcome_bad >= 0.20:
        recs.append("Outcome bad-rate high; review action-level routing policies and mailbox thresholds.")
    if not recs:
        recs.append("Learning loop stable; keep daily feedback cadence and monitor challenger impact score.")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_compare": {
            "uncertain_7d": uncertain7,
            "uncertain_14d": uncertain14,
            "uncertain_prev_7d": uncertain_prev7,
            "failed_7d": failed7,
            "failed_14d": failed14,
            "failed_prev_7d": failed_prev7,
            "llm_share_7d": float(s7.get("llm_share", 0.0) or 0.0),
            "llm_share_14d": float(s14.get("llm_share", 0.0) or 0.0),
        },
        "feedback": {
            "records": int(feedback.get("records", 0) or 0),
            "wrong_rate": wrong_rate,
            "outcome_bad_rate": outcome_bad,
        },
        "recommendations": recs,
    }


def render_md(payload: dict[str, Any]) -> str:
    wc = payload["window_compare"]
    fb = payload["feedback"]
    lines = [
        "# Learning Loop Weekly Summary",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Uncertain 7d/prev-7d: `{wc['uncertain_7d']}` / `{wc['uncertain_prev_7d']}`",
        f"- Failed 7d/prev-7d: `{wc['failed_7d']}` / `{wc['failed_prev_7d']}`",
        f"- LLM share 7d/14d: `{wc['llm_share_7d']}` / `{wc['llm_share_14d']}`",
        f"- Feedback records: `{fb['records']}`",
        f"- Wrong rate: `{fb['wrong_rate']}`",
        f"- Outcome bad rate: `{fb['outcome_bad_rate']}`",
        "",
        "## Recommendations",
    ]
    for rec in payload.get("recommendations", []):
        lines.append(f"- {rec}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate weekly learning-loop summary (current 7d vs previous 7d).")
    parser.add_argument("--state-db", default="data/multi-prod-state.sqlite")
    parser.add_argument("--audit-log", default="logs/multi-prod-audit.jsonl")
    parser.add_argument("--feedback-log", default="logs/quality-feedback.jsonl")
    parser.add_argument("--output-dir", default="logs/learning-loop")
    args = parser.parse_args()

    payload = build_summary(
        state_db=Path(args.state_db),
        audit_log=Path(args.audit_log),
        feedback_log=Path(args.feedback_log),
    )
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = _utc_stamp()
    jp = out / f"learning-loop-summary-{stamp}.json"
    mp = out / f"learning-loop-summary-{stamp}.md"
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mp.write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"json_path": str(jp), "markdown_path": str(mp), "generated_at": payload["generated_at"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
