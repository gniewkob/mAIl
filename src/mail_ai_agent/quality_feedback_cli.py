from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def append_feedback(
    *,
    feedback_log: Path,
    mailbox_id: str,
    message_id: str,
    predicted_category: str,
    corrected_category: str,
    reason: str | None,
    message_id_sha256: str | None = None,
    prediction_timestamp: str | None = None,
    outcome_ok: bool | None = None,
    outcome_note: str | None = None,
) -> dict[str, object]:
    feedback_log.parent.mkdir(parents=True, exist_ok=True)
    was_wrong = predicted_category.strip().lower() != corrected_category.strip().lower()
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mailbox_id": mailbox_id,
        "message_id": message_id,
        "predicted_category": predicted_category.strip().lower(),
        "corrected_category": corrected_category.strip().lower(),
        "was_wrong": was_wrong,
        "reason": (reason or "").strip(),
    }
    if message_id_sha256:
        payload["message_id_sha256"] = message_id_sha256.strip()
    if prediction_timestamp:
        payload["prediction_timestamp"] = prediction_timestamp.strip()
    if outcome_ok is not None:
        payload["outcome_ok"] = bool(outcome_ok)
    if outcome_note:
        payload["outcome_note"] = outcome_note.strip()
    with feedback_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Append manual operator feedback for learning impact scoring.")
    parser.add_argument("--feedback-log", default="logs/quality-feedback.jsonl", help="Path to feedback JSONL")
    parser.add_argument("--mailbox-id", required=True, help="Mailbox identifier")
    parser.add_argument("--message-id", required=True, help="Message identifier")
    parser.add_argument("--predicted-category", required=True, help="Model/system predicted category")
    parser.add_argument("--corrected-category", required=True, help="Operator corrected category")
    parser.add_argument("--reason", default="", help="Optional short reason for correction")
    parser.add_argument("--message-id-sha256", default="", help="Optional hashed message-id for correlation")
    parser.add_argument("--prediction-timestamp", default="", help="Optional original prediction timestamp (ISO-8601)")
    parser.add_argument("--outcome-ok", choices=["true", "false"], default="", help="Optional operator outcome label")
    parser.add_argument("--outcome-note", default="", help="Optional outcome note")
    args = parser.parse_args()

    payload = append_feedback(
        feedback_log=Path(args.feedback_log),
        mailbox_id=args.mailbox_id,
        message_id=args.message_id,
        predicted_category=args.predicted_category,
        corrected_category=args.corrected_category,
        reason=args.reason,
        message_id_sha256=args.message_id_sha256 or None,
        prediction_timestamp=args.prediction_timestamp or None,
        outcome_ok=(None if not args.outcome_ok else args.outcome_ok == "true"),
        outcome_note=args.outcome_note or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
