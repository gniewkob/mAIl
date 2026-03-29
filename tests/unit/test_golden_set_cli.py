from __future__ import annotations

from pathlib import Path

from mail_ai_agent.golden_set_cli import run_golden_set


def test_run_golden_set_passes_reference_dataset() -> None:
    payload = run_golden_set(Path("tests/synthetic_data/golden_batch_001.json"))

    assert payload["summary"]["total"] == 6
    assert payload["summary"]["failed"] == 0
    assert payload["summary"]["pass_rate"] == 1.0
    assert all(row["ok"] for row in payload["rows"])
