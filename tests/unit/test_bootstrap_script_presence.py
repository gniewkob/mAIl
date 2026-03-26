from __future__ import annotations

from pathlib import Path


def test_bootstrap_script_exists() -> None:
    path = Path("scripts/bootstrap.sh")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "pip install -r requirements.txt" in content
    assert "pytest -q" in content
    assert "mail_ai_agent.report_cli" in content
