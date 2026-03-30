from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from mail_ai_agent.draft_store import DraftStore
from mail_ai_agent.schemas import FinalDecision, ParsedEmail, WorkflowStatus


def _make_email(subject: str = "Hello World", sender: str = "user@example.com") -> ParsedEmail:
    return ParsedEmail(subject=subject, sender=sender)


def _make_decision(draft_reply: str = "Thanks for your message.") -> FinalDecision:
    return FinalDecision(
        category="question",
        target_folder="INBOX.Questions",
        final_status=WorkflowStatus.PROCESSED,
        action_taken="move_question",
        draft_reply=draft_reply,
        summary="A question was asked.",
    )


def test_draft_store_save_creates_file(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    email = _make_email()
    decision = _make_decision()
    path = store.save(email, decision, fingerprint="abc12345")

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["subject"] == "Hello World"
    assert data["sender"] == "user@example.com"
    assert data["draft_reply"] == "Thanks for your message."
    assert data["category"] == "question"


def test_draft_store_save_redact_pii_hides_fields(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    email = _make_email(subject="Urgent question", sender="client@example.com")
    decision = _make_decision()
    path = store.save(email, decision, fingerprint="abc12345", redact_pii=True)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["subject"] == "[redacted]"
    assert data["sender"] == "[redacted]"
    assert "subject_sha256" in data
    assert "sender_sha256" in data
    assert len(data["subject_sha256"]) == 64  # sha256 hex digest


def test_draft_store_save_redact_pii_false_keeps_fields(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    email = _make_email(subject="Normal subject", sender="user@example.com")
    decision = _make_decision()
    path = store.save(email, decision, fingerprint="def67890", redact_pii=False)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["subject"] == "Normal subject"
    assert data["sender"] == "user@example.com"
    assert "subject_sha256" not in data
    assert "sender_sha256" not in data


def test_draft_store_save_sets_restricted_permissions(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    email = _make_email()
    decision = _make_decision()
    path = store.save(email, decision, fingerprint="perm1234")

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_draft_save_is_atomic(tmp_path, monkeypatch):
    """DraftStore.save() must write via tmp+os.replace, not direct write_text on the target."""
    write_text_calls = []
    original_write_text = Path.write_text

    def tracking_write_text(self, *args, **kwargs):
        write_text_calls.append(str(self))
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", tracking_write_text)

    store = DraftStore(tmp_path / "drafts")
    email = _make_email()
    decision = _make_decision()
    result_path = store.save(email, decision, "abc123")

    direct_writes = [p for p in write_text_calls if p == str(result_path)]
    assert not direct_writes, "DraftStore.save() must use tmp+os.replace, not direct write_text"
    assert result_path.exists()
