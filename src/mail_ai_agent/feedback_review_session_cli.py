from __future__ import annotations

import argparse
import json
from pathlib import Path

from .quality_feedback_cli import append_feedback

_ALLOWED = {
    "appointment",
    "question",
    "complaint",
    "spam",
    "newsletter",
    "offer",
    "billing",
    "system",
    "other",
}


def _load_candidates(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    rows = payload.get("candidates", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive feedback review session (y/n/s/q).")
    parser.add_argument("--candidates", default="logs/feedback-candidates-daily.json")
    parser.add_argument("--feedback-log", default="logs/quality-feedback.jsonl")
    parser.add_argument("--mailbox-id", default="review_session")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    candidates = _load_candidates(Path(args.candidates))[: max(1, args.limit)]
    if not candidates:
        print("No candidates found.")
        return

    appended = 0
    skipped = 0
    print(f"Loaded {len(candidates)} candidates. Commands: y=accept, n=correct, s=skip, q=quit")

    for i, row in enumerate(candidates, start=1):
        predicted = str(row.get("predicted_category") or "other").strip().lower()
        msg_hash = str(row.get("message_id_sha256") or "")
        ts = str(row.get("timestamp") or "")
        reason = str(row.get("review_reason") or "manual_review")
        conf = row.get("confidence", "")
        sender = str(row.get("sender_sha256") or "")
        source_folder = str(row.get("source_folder") or "")
        imap_uid = str(row.get("imap_uid") or "")
        state_status = str(row.get("state_status") or "")

        print("\n" + "-" * 72)
        print(f"[{i}/{len(candidates)}] predicted={predicted} confidence={conf} reason={reason}")
        if source_folder:
            print(f"source_folder={source_folder} imap_uid={imap_uid}")
        if state_status:
            print(f"state_status={state_status}")
        print(f"message_id_sha256={msg_hash}")
        print(f"sender_sha256={sender}")

        cmd = input("Decision [y/n/s/q]: ").strip().lower()
        if cmd == "q":
            break
        if cmd == "s" or cmd == "":
            skipped += 1
            continue

        corrected = predicted
        outcome_ok = True
        outcome_note = "manual_accept"
        note_reason = reason

        if cmd == "n":
            raw = input("Corrected category: ").strip().lower()
            if raw not in _ALLOWED:
                print("Invalid category, skipping.")
                skipped += 1
                continue
            corrected = raw
            outcome_ok = False
            outcome_note = "manual_correction"
            note_reason = f"manual_correction:{reason}"
        elif cmd != "y":
            print("Unknown command, skipping.")
            skipped += 1
            continue

        append_feedback(
            feedback_log=Path(args.feedback_log),
            mailbox_id=args.mailbox_id,
            message_id=f"sha256:{msg_hash}" if msg_hash else f"session:{i}",
            predicted_category=predicted,
            corrected_category=corrected,
            reason=note_reason,
            message_id_sha256=msg_hash or None,
            prediction_timestamp=ts or None,
            outcome_ok=outcome_ok,
            outcome_note=outcome_note,
        )
        appended += 1

    print(f"\nDone. appended={appended} skipped={skipped}")


if __name__ == "__main__":
    main()
