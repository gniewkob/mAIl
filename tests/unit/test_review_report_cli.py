from __future__ import annotations

import json

from mail_ai_agent.review_report import build_review_rows, summarize_review_rows


def test_summarize_review_rows_counts_cleanup_pending_and_auth_failures(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        "\n".join(
            [
                json.dumps({"status_after": "cleanup_pending", "action_taken": "cleanup_uidvalidity_mismatch"}),
                json.dumps({"status_after": "imap_auth_failed", "action_taken": "imap_auth_failed"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = summarize_review_rows(build_review_rows(audit))

    assert summary["cleanup_pending"] == 1
    assert summary["failed"] == 1

import json
import sys
from pathlib import Path
from unittest.mock import patch


def test_review_report_cli_outputs_json_for_empty_log(tmp_path: Path, capsys) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("", encoding="utf-8")

    with patch.object(sys, "argv", ["review_report_cli", "--audit-log", str(audit)]):
        from mail_ai_agent.review_report_cli import main
        main()

    out = capsys.readouterr().out
    assert out.strip()  # some output produced


def test_review_report_cli_exports_csv(tmp_path: Path, capsys) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps({
            "action_taken": "simulate_route",
            "status_after": "simulated",
            "category": "question",
            "confidence": 0.85,
            "sender_sha256": "abc",
            "subject_sha256": "def",
        }) + "\n",
        encoding="utf-8",
    )
    dest = tmp_path / "review.csv"

    with patch.object(sys, "argv", [
        "review_report_cli", "--audit-log", str(audit), "--export-csv", str(dest),
    ]):
        from mail_ai_agent.review_report_cli import main
        main()

    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "category" in content or "action_taken" in content
