from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.quality_learning_cli import (
    build_quality_learning_payload,
    render_quality_learning_markdown,
    run_quality_learning,
)
from mail_ai_agent.state_manager import StateManager


def test_build_quality_learning_payload_summarizes_current_state_and_audit(tmp_path: Path) -> None:
    state_db = tmp_path / "state.sqlite"
    audit_log = tmp_path / "audit.jsonl"
    manager = StateManager(state_db)

    lease = manager.acquire_lease(
        mailbox_id="mbox_a",
        message_id="<one@mailplus.pl>",
        fingerprint="fp1",
        imap_uid="1",
        sender="a@example.com",
        subject="Hello",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="w1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease.record is not None
    manager.mark_uncertain(lease.record.id, category="other", confidence=0.4, error_message="manual review")

    lease_2 = manager.acquire_lease(
        mailbox_id="mbox_a",
        message_id="<two@mailplus.pl>",
        fingerprint="fp2",
        imap_uid="2",
        sender="b@example.com",
        subject="Hello again",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="w1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease_2.record is not None
    manager.mark_uncertain(lease_2.record.id, category="other", confidence=0.4, error_message="manual review")

    audit_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-08T12:00:00+00:00",
                        "mailbox_id": "mbox_a",
                        "category": "other",
                        "action_taken": "move_route_uncertain",
                        "status_after": "uncertain",
                        "target_folder": "INBOX.AI-Uncertain",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-08T12:00:01+00:00",
                        "mailbox_id": "mbox_b",
                        "category": "parse_error",
                        "action_taken": "move_route_uncertain_parse_failure",
                        "status_after": "uncertain",
                        "target_folder": "INBOX.AI-Uncertain",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_quality_learning_payload(state_db=state_db, audit_log=audit_log)

    assert payload["state"]["uncertain"] == 2
    assert payload["current_uncertain_by_mailbox"] == {"mbox_a": 2}
    assert payload["audit_summary"]["uncertain"] == 2
    assert payload["recent_uncertain_action_mix"]["move_route_uncertain"] == 1
    assert "Inspect parse_error history" in " ".join(payload["recommendations"])
    assert payload["proposal_counts"]["rule_engine"] == 1
    assert payload["proposal_counts"]["prompt"] == 1
    assert payload["proposal_counts"]["parser"] == 1
    rule_engine_proposal = next(item for item in payload["proposals"] if item["kind"] == "rule_engine")
    assert rule_engine_proposal["patch_target"] == "src/mail_ai_agent/rule_engine.py"
    assert "mailplus.pl" in rule_engine_proposal["patch_basis"]
    assert "LOW_SIGNAL_MESSAGE_ID_REGEX" in rule_engine_proposal["patch_diff"]


def test_run_quality_learning_writes_json_and_markdown(tmp_path: Path) -> None:
    state_db = tmp_path / "state.sqlite"
    audit_log = tmp_path / "audit.jsonl"
    out_dir = tmp_path / "reports"
    audit_log.write_text("", encoding="utf-8")

    result = run_quality_learning(state_db=state_db, audit_log=audit_log, output_dir=out_dir)

    json_path = Path(result["json_path"])
    md_path = Path(result["markdown_path"])
    assert result["patch_paths"] == []
    assert json_path.exists()
    assert md_path.exists()
    assert "Quality Learning Report" in md_path.read_text(encoding="utf-8")


def test_run_quality_learning_writes_patch_when_rule_engine_proposal_has_diff(tmp_path: Path) -> None:
    state_db = tmp_path / "state.sqlite"
    audit_log = tmp_path / "audit.jsonl"
    out_dir = tmp_path / "reports"
    manager = StateManager(state_db)

    for idx in range(2):
        lease = manager.acquire_lease(
            mailbox_id="mbox_a",
            message_id=f"<mailplus-{idx}@mailplus.pl>",
            fingerprint=f"fp{idx}",
            imap_uid=str(idx + 1),
            sender="a@example.com",
            subject="Hello",
            source_folder="INBOX.AI-Review",
            internaldate=None,
            worker_id="w1",
            lease_seconds=60,
            max_retries=3,
        )
        assert lease.record is not None
        manager.mark_uncertain(lease.record.id, category="other", confidence=0.4, error_message="manual review")

    audit_log.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-08T12:00:00+00:00",
                "mailbox_id": "mbox_a",
                "category": "other",
                "action_taken": "move_route_uncertain",
                "status_after": "uncertain",
                "target_folder": "INBOX.AI-Uncertain",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_quality_learning(state_db=state_db, audit_log=audit_log, output_dir=out_dir)

    assert len(result["patch_paths"]) == 1
    patch_path = Path(result["patch_paths"][0])
    assert patch_path.exists()
    content = patch_path.read_text(encoding="utf-8")
    assert "rule_engine.py" in content
    assert "mailplus\\.pl" in content


def test_render_quality_learning_markdown_contains_recommendations() -> None:
    payload = {
        "generated_at": "2026-04-08T12:00:00+00:00",
        "state": {"processed": 10, "uncertain": 2, "failed": 0, "cleanup_pending": 0},
        "top_uncertain_mailboxes": [{"key": "mbox_a", "count": 2}],
        "recent_uncertain_action_mix": {"move_route_uncertain": 2},
        "proposals": [
            {
                "kind": "rule_engine",
                "priority": "medium",
                "title": "Review uncertain mailbox",
                "suggested_change": "Promote stable patterns into rules.",
            }
        ],
        "recommendations": ["Review the top uncertain mailbox first."],
    }

    rendered = render_quality_learning_markdown(payload)

    assert "mbox_a" in rendered
    assert "Review the top uncertain mailbox first." in rendered
    assert "## Proposed changes" in rendered
