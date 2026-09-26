from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.quality_feedback_cli import append_feedback


def test_append_feedback_writes_jsonl_record(tmp_path: Path) -> None:
    feedback_log = tmp_path / "quality-feedback.jsonl"
    payload = append_feedback(
        feedback_log=feedback_log,
        mailbox_id="mbox_a",
        message_id="<1@example.com>",
        predicted_category="other",
        corrected_category="offer",
        reason="manual review correction",
    )
    assert payload["was_wrong"] is True
    lines = feedback_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["mailbox_id"] == "mbox_a"
    assert saved["predicted_category"] == "other"
    assert saved["corrected_category"] == "offer"
