from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.quality_report import _QUALITY_CACHE, build_quality_payload


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
    assert payload["summary"]["routed_records"] == 3
    assert payload["by_mailbox"]["kontakt"] == 2
    assert payload["by_route_source"]["llm_failure"] == 1
    assert "other" not in payload["by_route_source"]
    assert payload["recent_uncertain"][0]["subject"] == "sha256:subjecthash"
    assert payload["recent_failures"][0]["action_taken"] == "mailbox_failed"


def test_build_quality_payload_counts_imap_auth_failure_as_failure(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-03-29T12:15:00+00:00",
                        "mailbox_id": "kontakt",
                        "action_taken": "imap_auth_failed",
                        "status_after": "imap_auth_failed",
                        "error": "AUTHENTICATIONFAILED",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-03-29T12:20:00+00:00",
                        "mailbox_id": "kontakt",
                        "action_taken": "move_route_from_llm",
                        "status_after": "processed",
                        "category": "question",
                        "target_folder": "INBOX.Questions",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_quality_payload(audit_path)

    assert payload["summary"]["failed"] == 1
    assert payload["recent_failures"][0]["action_taken"] == "imap_auth_failed"


def test_build_quality_payload_keeps_parse_error_as_first_class_category(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-03-29T12:15:00+00:00",
                "mailbox_id": "kontakt",
                "action_taken": "move_route_uncertain_parse_failure",
                "status_after": "uncertain",
                "category": "parse_error",
                "target_folder": "INBOX.AI-Uncertain",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_quality_payload(audit_path)

    assert payload["by_category"]["parse_error"] == 1
    assert payload["by_route_source"]["uncertain"] == 1


def test_build_quality_payload_updates_incrementally_for_appended_audit_records(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    _QUALITY_CACHE.clear()
    audit_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-03-29T12:00:00+00:00",
                "mailbox_id": "kontakt",
                "category": "question",
                "action_taken": "move_route_from_llm",
                "status_after": "processed",
                "target_folder": "INBOX.Questions",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    first = build_quality_payload(audit_path)
    assert first["summary"]["records"] == 1
    assert first["by_category"]["question"] == 1

    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": "2026-03-29T12:05:00+00:00",
                    "mailbox_id": "kontakt",
                    "category": "newsletter",
                    "action_taken": "move_skip_ai",
                    "status_after": "processed",
                    "target_folder": "INBOX.Newsletter",
                }
            )
            + "\n"
        )

    second = build_quality_payload(audit_path)
    assert second["summary"]["records"] == 2
    assert second["by_category"]["question"] == 1
    assert second["by_category"]["newsletter"] == 1

def test_build_quality_payload_normalizes_parse_failure_cleanup_pending_into_parse_error(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    _QUALITY_CACHE.clear()
    audit_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-03-29T12:15:00+00:00",
                "mailbox_id": "kontakt",
                "action_taken": "move_copy_succeeded_cleanup_pending",
                "status_after": "cleanup_pending",
                "category": "other",
                "target_folder": "INBOX.AI-Uncertain",
                "error": "parse_failed: broken mime; cleanup_failed: delete failed",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_quality_payload(audit_path)

    assert payload["by_category"]["parse_error"] == 1
