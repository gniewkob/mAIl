from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.feedback_autopilot_cli import run_autopilot


def test_run_autopilot_appends_conflict_feedback(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    feedback = tmp_path / "quality-feedback.jsonl"
    audit.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:00:00+00:00",
                        "mailbox_id": "m1",
                        "message_id_sha256": "abc",
                        "fingerprint": "fp1",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "category": "newsletter",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:05:00+00:00",
                        "mailbox_id": "m1",
                        "message_id_sha256": "abc",
                        "fingerprint": "fp1",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "category": "offer",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_autopilot(audit_log=audit, feedback_log=feedback, window_hours=999999, max_appends=10)
    assert result["appended"] == 1
    rows = feedback.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    item = json.loads(rows[0])
    assert item["was_wrong"] is True
    assert item["reason"] == "autopilot_conflict_reclassification"
    assert item["prediction_timestamp"] == "2026-05-26T00:00:00+00:00"


def test_run_autopilot_skips_cross_mailbox_and_fingerprint_mismatch(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    feedback = tmp_path / "quality-feedback.jsonl"
    audit.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:00:00+00:00",
                        "mailbox_id": "m1",
                        "message_id_sha256": "same",
                        "fingerprint": "fp1",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "category": "newsletter",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:01:00+00:00",
                        "mailbox_id": "m2",
                        "message_id_sha256": "same",
                        "fingerprint": "fp1",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "category": "offer",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:02:00+00:00",
                        "mailbox_id": "m1",
                        "message_id_sha256": "abc",
                        "fingerprint": "fp_a",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "category": "newsletter",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:03:00+00:00",
                        "mailbox_id": "m1",
                        "message_id_sha256": "abc",
                        "fingerprint": "fp_b",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "category": "offer",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = run_autopilot(audit_log=audit, feedback_log=feedback, window_hours=999999, max_appends=10)
    assert result["appended"] == 0


def test_run_autopilot_skips_oscillating_categories(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    feedback = tmp_path / "quality-feedback.jsonl"
    audit.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:00:00+00:00",
                        "mailbox_id": "m1",
                        "message_id_sha256": "osc",
                        "fingerprint": "fp1",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "category": "newsletter",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:01:00+00:00",
                        "mailbox_id": "m1",
                        "message_id_sha256": "osc",
                        "fingerprint": "fp1",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "category": "offer",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:02:00+00:00",
                        "mailbox_id": "m1",
                        "message_id_sha256": "osc",
                        "fingerprint": "fp1",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "category": "social",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = run_autopilot(audit_log=audit, feedback_log=feedback, window_hours=999999, max_appends=10)
    assert result["appended"] == 0
