from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from mail_ai_agent.report_cli import main


def test_report_cli_outputs_json(capsys, tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    state_path = tmp_path / "state.sqlite"

    with (
        patch("mail_ai_agent.report_cli.load_audit_records") as mock_load_audit,
        patch("mail_ai_agent.report_cli.summarize_audit_records") as mock_summarize_audit,
        patch("mail_ai_agent.report_cli.summarize_state") as mock_summarize_state,
        patch.object(sys, "argv", ["report_cli", "--audit-log", str(audit_path), "--state-db", str(state_path)]),
    ):
        mock_load_audit.return_value = [{"some": "record"}]
        mock_summarize_audit.return_value = {"audit": "summary"}
        mock_summarize_state.return_value = {"state": "summary"}

        main()

        mock_load_audit.assert_called_once_with(audit_path)
        mock_summarize_audit.assert_called_once_with([{"some": "record"}])
        mock_summarize_state.assert_called_once_with(state_path)

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload == {
        "audit": {"audit": "summary"},
        "state": {"state": "summary"},
    }


def test_report_cli_exports_csv(capsys, tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    state_path = tmp_path / "state.sqlite"
    audit_csv = tmp_path / "audit.csv"
    state_csv = tmp_path / "state.csv"

    with (
        patch("mail_ai_agent.report_cli.load_audit_records") as mock_load_audit,
        patch("mail_ai_agent.report_cli.summarize_audit_records") as mock_summarize_audit,
        patch("mail_ai_agent.report_cli.summarize_state") as mock_summarize_state,
        patch("mail_ai_agent.report_cli.export_audit_csv") as mock_export_audit_csv,
        patch("mail_ai_agent.report_cli.export_state_csv") as mock_export_state_csv,
        patch.object(
            sys,
            "argv",
            [
                "report_cli",
                "--audit-log",
                str(audit_path),
                "--state-db",
                str(state_path),
                "--export-audit-csv",
                str(audit_csv),
                "--export-state-csv",
                str(state_csv),
            ],
        ),
    ):
        records = [{"some": "record"}]
        mock_load_audit.return_value = records
        mock_summarize_audit.return_value = {"audit": "summary"}
        mock_summarize_state.return_value = {"state": "summary"}

        main()

        mock_export_audit_csv.assert_called_once_with(records, audit_csv)
        mock_export_state_csv.assert_called_once_with(state_path, state_csv)

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload == {
        "audit": {"audit": "summary"},
        "state": {"state": "summary"},
    }
