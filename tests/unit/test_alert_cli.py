from __future__ import annotations

import sys

import pytest

from mail_ai_agent.alert_cli import build_alert_message


def test_build_alert_message_for_ok_payload() -> None:
    payload = {
        "ok": True,
        "issues": [],
        "state": {
            "processed": 10,
            "uncertain": 0,
            "uncertain_error": 0,
            "uncertain_exploration": 0,
            "failed": 0,
            "cleanup_pending": 0,
        },
    }

    message = build_alert_message(payload, service_name="mail-ai-prod")

    assert "[OK] mail-ai-prod" in message
    assert "processed=10" in message
    assert "uncertain_error=0" in message
    assert "uncertain_exploration=0" in message
    assert "issues:" not in message


def test_build_alert_message_for_unhealthy_payload() -> None:
    payload = {
        "ok": False,
        "issues": ["recent mailbox_failed present in audit log", "state_cleanup_pending=1"],
        "state": {
            "processed": 10,
            "uncertain": 1,
            "uncertain_error": 1,
            "uncertain_exploration": 0,
            "failed": 0,
            "cleanup_pending": 1,
        },
    }

    message = build_alert_message(payload, service_name="mail-ai-prod")

    assert "[ALERT] mail-ai-prod" in message
    assert "issues:" in message
    assert "uncertain_error=1" in message
    assert "- recent mailbox_failed present in audit log" in message


def test_alert_cli_passes_env_file_to_health_payload(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict[str, object] = {}

    def fake_build_health_payload(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "issues": [],
            "state": {
                "processed": 1,
                "uncertain": 0,
                "uncertain_error": 0,
                "uncertain_exploration": 0,
                "failed": 0,
                "cleanup_pending": 0,
            },
        }

    monkeypatch.setattr("mail_ai_agent.alert_cli.build_health_payload", fake_build_health_payload)
    monkeypatch.setattr(sys, "argv", ["alert_cli", "--env-file", ".env.multi.prod"])

    from mail_ai_agent.alert_cli import main

    main()
    capsys.readouterr()
    assert str(captured["env_file"]).endswith(".env.multi.prod")
    assert captured["max_uncertain_error"] == 0
    assert captured["max_uncertain_exploration"] == 0
