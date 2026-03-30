from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch


def test_quality_report_cli_outputs_json_for_empty_log(tmp_path: Path, capsys) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("", encoding="utf-8")

    with patch.object(sys, "argv", ["quality_report_cli", "--audit-log", str(audit)]):
        from mail_ai_agent.quality_report_cli import main
        main()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, dict)


def test_quality_report_cli_outputs_json_for_nonempty_log(tmp_path: Path, capsys) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps({
            "action_taken": "move_route_from_llm",
            "status_after": "processed",
            "category": "question",
            "confidence": 0.9,
        }) + "\n",
        encoding="utf-8",
    )

    with patch.object(sys, "argv", ["quality_report_cli", "--audit-log", str(audit)]):
        from mail_ai_agent.quality_report_cli import main
        main()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, dict)
