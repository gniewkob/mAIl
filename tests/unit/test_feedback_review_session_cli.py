from __future__ import annotations

from pathlib import Path
from email.message import EmailMessage
import hashlib

import pytest

from mail_ai_agent.feedback_review_session_cli import _load_candidates


def test_load_candidates_handles_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not-json", encoding="utf-8")
    assert _load_candidates(path) == []


def test_preview_decodes_message_and_removes_terminal_controls() -> None:
    from mail_ai_agent.feedback_review_session_cli import _message_preview

    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["Subject"] = "Review subject"
    message["Message-ID"] = "<review@example.com>"
    message.set_content("Useful text\x1b[2J with details")
    digest = hashlib.sha256(b"<review@example.com>").hexdigest()
    preview = _message_preview(message.as_bytes(), digest)
    assert preview["sender"] == "sender@example.com"
    assert preview["subject"] == "Review subject"
    assert "Useful text" in preview["body"]
    assert "\x1b" not in preview["body"]


def test_preview_rejects_wrong_message() -> None:
    from mail_ai_agent.feedback_review_session_cli import _message_preview

    with pytest.raises(ValueError, match="identity"):
        _message_preview(b"Message-ID: <wrong@example.com>\r\n\r\nText", "0" * 64)


def test_preview_html_is_text_and_attachment_is_not_shown() -> None:
    from mail_ai_agent.feedback_review_session_cli import _message_preview

    message = EmailMessage()
    message["Message-ID"] = "<html@example.com>"
    message.set_content("<p>Hello review</p><script>hidden script</script>", subtype="html")
    message.add_attachment(b"hidden attachment", maintype="text", subtype="plain", filename="private.txt")
    preview = _message_preview(message.as_bytes(), hashlib.sha256(b"<html@example.com>").hexdigest())
    assert "Hello review" in preview["body"]
    assert "hidden script" not in preview["body"]
    assert "hidden attachment" not in preview["body"]


def test_state_locator_requires_mailbox_and_hash_match(tmp_path: Path) -> None:
    import sqlite3
    from mail_ai_agent.feedback_review_session_cli import _state_locator

    path = tmp_path / "state.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE email_processing_state (id, mailbox_id, message_id, source_folder, target_folder)")
        conn.execute("INSERT INTO email_processing_state VALUES (1, 'mb', '<test@example.com>', 'INBOX', 'Archive')")
    row = {"mailbox_id": "mb", "state_record_id": 1,
           "message_id_sha256": hashlib.sha256(b"<test@example.com>").hexdigest()}
    assert _state_locator(row, path)["target_folder"] == "Archive"
    assert _state_locator({**row, "mailbox_id": "wrong"}, path) == {}
    assert _state_locator({**row, "message_id_sha256": "0" * 64}, path) == {}


@pytest.mark.parametrize("available", [True, False])
def test_review_saves_only_verified_preview_and_real_mailbox(tmp_path: Path, monkeypatch, available: bool) -> None:
    import json
    import sys
    from types import SimpleNamespace
    from mail_ai_agent import feedback_review_session_cli as review

    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": [{"mailbox_id": "mb", "predicted_category": "newsletter",
                                                    "message_id_sha256": "a" * 64}]}))
    feedback = tmp_path / "feedback.jsonl"
    monkeypatch.setattr(sys, "argv", ["review", "--candidates", str(candidates), "--feedback-log", str(feedback)])
    monkeypatch.setattr(review, "Settings", lambda **kwargs: SimpleNamespace(state_db_path=tmp_path / "state.sqlite"))

    def preview(*args):
        if not available:
            raise ValueError("missing")
        return {"sender": "private@example.com", "subject": "Private subject", "body": "Private body"}

    monkeypatch.setattr(review, "_load_preview", preview)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    review.main()
    if available:
        text = feedback.read_text()
        assert json.loads(text)["mailbox_id"] == "mb"
        assert "private@example.com" not in text
        assert "Private subject" not in text
        assert "Private body" not in text
    else:
        assert not feedback.exists()
