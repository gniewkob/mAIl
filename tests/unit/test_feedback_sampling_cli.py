from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.feedback_sampling_cli import build_feedback_candidates
from mail_ai_agent.state_manager import StateManager


def test_build_feedback_candidates_prioritizes_low_conf_and_novel_sender(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    out = tmp_path / "candidates.json"
    audit.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:00:00+00:00",
                        "mailbox_id": "m1",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "sender_sha256": "s-low",
                        "subject_sha256": "sub1",
                        "message_id_sha256": "mid1",
                        "category": "other",
                        "confidence": 0.8,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-26T00:01:00+00:00",
                        "mailbox_id": "m1",
                        "status_after": "processed",
                        "action_taken": "tag_route_from_llm",
                        "sender_sha256": "s-new",
                        "subject_sha256": "sub2",
                        "message_id_sha256": "mid2",
                        "category": "newsletter",
                        "confidence": 0.99,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_feedback_candidates(
        audit_log=audit,
        env_file=None,
        state_db=None,
        output_path=out,
        window_hours=999999,
        limit=10,
        low_conf_threshold=0.96,
    )

    assert payload["scanned"] == 2
    candidates = payload["candidates"]
    assert len(candidates) == 2
    assert candidates[0]["review_reason"] == "low_confidence"
    assert any(row["review_reason"] == "novel_sender" for row in candidates)
    assert out.exists()


def test_build_feedback_candidates_includes_state_uncertain_backlog(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    out = tmp_path / "candidates.json"
    state_db = tmp_path / "state.sqlite"
    audit.write_text("", encoding="utf-8")

    manager = StateManager(state_db)
    lease = manager.acquire_lease(
        mailbox_id="m1",
        message_id="<mid-1@example.com>",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Temat",
        source_folder="INBOX",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease.record is not None
    manager.mark_uncertain(
        lease.record.id,
        category="newsletter",
        confidence=0.95,
        action_taken="copy_tag_route_uncertain_exploration",
    )

    payload = build_feedback_candidates(
        audit_log=audit,
        env_file=None,
        state_db=state_db,
        output_path=out,
        window_hours=24,
        limit=10,
        low_conf_threshold=0.96,
    )

    assert payload["state_uncertain_candidates"] == 1
    assert payload["candidates"][0]["review_reason"] == "state_uncertain_backlog"
    assert payload["candidates"][0]["predicted_category"] == "newsletter"


def test_build_feedback_candidates_includes_imap_folder_candidates(tmp_path: Path, monkeypatch) -> None:
    audit = tmp_path / "audit.jsonl"
    out = tmp_path / "candidates.json"
    state_db = tmp_path / "state.sqlite"
    env_file = tmp_path / ".env"
    audit.write_text("", encoding="utf-8")
    env_file.write_text("", encoding="utf-8")

    manager = StateManager(state_db)
    lease = manager.acquire_lease(
        mailbox_id="m1",
        message_id="<mid-2@example.com>",
        fingerprint="fp-2",
        imap_uid="20",
        sender="client2@example.com",
        subject="Temat 2",
        source_folder="INBOX.AI-Uncertain",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease.record is not None
    manager.mark_uncertain(
        lease.record.id,
        category="billing",
        confidence=0.88,
        action_taken="move_route_uncertain",
    )

    class FakeMailbox:
        mailbox_id = "m1"
        imap_uncertain_folder = "INBOX.AI-Uncertain"

    class FakeSettings:
        imap_tag_prefix = "$AI_"

        def __init__(self, _env_file=None) -> None:
            pass

        def load_mailboxes(self):
            return [FakeMailbox()]

    class FakeIMAPClient:
        def __init__(self, mailbox) -> None:
            self.mailbox = mailbox

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def list_uids(self, folder: str):
            assert folder == "INBOX.AI-Uncertain"
            return ["20"]

        def fetch_message_id_headers(self, folder: str, uids: list[str]):
            assert folder == "INBOX.AI-Uncertain"
            return {"20": "<mid-2@example.com>"}

        def fetch_uid_keywords(self, folder: str, uids: list[str]):
            return {"20": {"$AI_billing"}}

    monkeypatch.setattr("mail_ai_agent.feedback_sampling_cli.Settings", FakeSettings)
    monkeypatch.setattr("mail_ai_agent.feedback_sampling_cli.IMAPClient", FakeIMAPClient)

    payload = build_feedback_candidates(
        audit_log=audit,
        env_file=env_file,
        state_db=state_db,
        output_path=out,
        mailbox_ids={"m1"},
        imap_folders=["INBOX.AI-Uncertain"],
        imap_per_folder_limit=5,
        window_hours=24,
        limit=10,
        low_conf_threshold=0.96,
    )

    assert payload["imap_candidates"] == 1
    assert payload["imap_errors"] == []
    assert any(row["review_reason"] == "imap_uncertain_folder" for row in payload["candidates"])
