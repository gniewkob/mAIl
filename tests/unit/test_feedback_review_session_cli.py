from __future__ import annotations

from pathlib import Path

from mail_ai_agent.feedback_review_session_cli import _load_candidates


def test_load_candidates_handles_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not-json", encoding="utf-8")
    assert _load_candidates(path) == []
