from __future__ import annotations

from pathlib import Path

import pytest

from mail_ai_agent.learning_loop_summary_cli import build_summary, render_md


def test_build_summary_compares_current_vs_previous_7d(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import mail_ai_agent.learning_loop_summary_cli as summary

    def _fake_quality_payload(_audit_log: Path, *, window_days: int) -> dict[str, object]:
        if window_days == 7:
            return {"summary": {"uncertain": 6, "failed": 3, "llm_share": 0.8}}
        return {"summary": {"uncertain": 10, "failed": 5, "llm_share": 0.75}}

    monkeypatch.setattr(summary, "build_quality_payload", _fake_quality_payload)
    monkeypatch.setattr(
        summary,
        "build_quality_payload_between",
        lambda _audit_log, *, start_days_ago, end_days_ago: {"summary": {"uncertain": 4, "failed": 2, "llm_share": 0.7}},
    )
    monkeypatch.setattr(
        summary,
        "build_quality_learning_payload",
        lambda **kwargs: {"feedback_summary": {"records": 5, "wrong_rate": 0.0, "outcome_bad_rate": 0.0}},
    )

    payload = build_summary(
        state_db=tmp_path / "state.sqlite",
        audit_log=tmp_path / "audit.jsonl",
        feedback_log=tmp_path / "quality-feedback.jsonl",
    )

    wc = payload["window_compare"]
    assert wc["uncertain_prev_7d"] == 4
    assert wc["failed_prev_7d"] == 2
    assert any("current 7d vs previous 7d" in r for r in payload["recommendations"])
    md = render_md(payload)
    assert "Uncertain 7d/prev-7d" in md
    assert "Failed 7d/prev-7d" in md
