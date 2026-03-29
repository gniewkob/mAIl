from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.quality_report import build_quality_payload


def test_build_quality_payload_summarizes_route_sources_and_recent_issues(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-03-29T12:00:00+00:00",
                        "mailbox_id": "kontakt",
                        "category": "billing",
                        "action_taken": "move_skip_ai",
                        "status_after": "processed",
                        "target_folder": "INBOX.Billing",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-03-29T12:05:00+00:00",
                        "mailbox_id": "kontakt",
                        "category": "question",
                        "action_taken": "move_route_from_llm",
                        "status_after": "processed",
                        "target_folder": "INBOX.Questions",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-03-29T12:10:00+00:00",
                        "mailbox_id": "shop",
                        "category": "other",
                        "action_taken": "move_route_uncertain_llm_failure",
                        "status_after": "uncertain",
                        "target_folder": "INBOX.AI-Uncertain",
                        "subject_sha256": "subjecthash",
                        "sender_sha256": "senderhash",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-03-29T12:15:00+00:00",
                        "mailbox_id": "shop",
                        "action_taken": "mailbox_failed",
                        "status_after": "mailbox_failed",
                        "error": "boom",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_quality_payload(audit_path)

    assert payload["summary"]["records"] == 4
    assert payload["summary"]["rule_routed"] == 1
    assert payload["summary"]["llm_routed"] == 1
    assert payload["by_mailbox"]["kontakt"] == 2
    assert payload["by_route_source"]["llm_failure"] == 1
    assert payload["recent_uncertain"][0]["subject"] == "sha256:subjecthash"
    assert payload["recent_failures"][0]["action_taken"] == "mailbox_failed"
