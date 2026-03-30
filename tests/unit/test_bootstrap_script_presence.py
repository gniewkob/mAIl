from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent


def test_bootstrap_script_exists() -> None:
    path = _REPO_ROOT / "scripts" / "bootstrap.sh"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "pip install -r requirements.txt" in content
    assert "pytest -q" in content
    assert "mail_ai_agent.report_cli" in content
    assert "mail_ai_agent.preflight_cli" in content
    assert "prod_healthcheck.sh" in content
    assert "prod_canary.sh" in content
    assert "prod_alert.sh" in content
