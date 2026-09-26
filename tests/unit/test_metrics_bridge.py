from __future__ import annotations

import sys
from pathlib import Path


def test_metrics_bridge_script_uses_bridge_module() -> None:
    script = Path("scripts/prod_metrics.sh").read_text(encoding="utf-8")
    assert "mail_ai_agent.metrics_bridge" in script
    assert 'ENV_FILE="${ENV_FILE:-.env.multi.prod}"' in script
    assert 'MAX_UNCERTAIN_ERROR="${MAX_UNCERTAIN_ERROR:-0}"' in script
    assert "--max-uncertain-error" in script
    assert "--max-uncertain-exploration" in script


def test_metrics_bridge_plist_uses_bridge_module() -> None:
    plist = Path("com.mailai.metrics.prod.plist").read_text(encoding="utf-8")
    assert "mail_ai_agent.metrics_bridge" in plist
    assert "--max-uncertain-error" in plist
    assert "--max-uncertain-exploration" in plist


def test_metrics_bridge_returns_500_on_payload_failure() -> None:
    import mail_ai_agent.metrics_bridge as metrics_bridge

    called: dict[str, object] = {}

    def fake_serve_metrics(
        *,
        host: str,
        port: int,
        payload_builder,
        trigger_token,
        trigger_link_token,
        trigger_label,
        trigger_allowlist=None,
        trigger_link_origin_allowlist=None,
        trigger_cooldown_seconds=60,
        trigger_link_unauthorized_cooldown_seconds=15,
        trigger_audit_log=None,
        trigger_audit_max_bytes=2_000_000,
        trigger_audit_backup_count=5,
        payload_refresh_seconds=300,
    ) -> None:
        called["host"] = host
        called["port"] = port
        called["payload_builder"] = payload_builder
        called["trigger_token"] = trigger_token
        called["trigger_link_token"] = trigger_link_token
        called["trigger_label"] = trigger_label
        called["trigger_allowlist"] = trigger_allowlist
        called["trigger_link_origin_allowlist"] = trigger_link_origin_allowlist
        called["trigger_cooldown_seconds"] = trigger_cooldown_seconds
        called["trigger_link_unauthorized_cooldown_seconds"] = trigger_link_unauthorized_cooldown_seconds
        called["trigger_audit_log"] = trigger_audit_log
        called["trigger_audit_max_bytes"] = trigger_audit_max_bytes
        called["trigger_audit_backup_count"] = trigger_audit_backup_count
        called["payload_refresh_seconds"] = payload_refresh_seconds

    original_serve_metrics = metrics_bridge.serve_metrics
    metrics_bridge.serve_metrics = fake_serve_metrics
    try:
        metrics_bridge.serve_bridge(
            host="127.0.0.1",
            port=9177,
            payload_builder=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            trigger_token="abc",
            trigger_label="com.mailai.autotune.weekly.prod",
        )
    finally:
        metrics_bridge.serve_metrics = original_serve_metrics

    assert called["host"] == "127.0.0.1"
    assert called["port"] == 9177
    assert callable(called["payload_builder"])
    assert called["trigger_token"] == "abc"
    assert called["trigger_link_token"] is None
    assert called["trigger_label"] == "com.mailai.autotune.weekly.prod"
    assert called["payload_refresh_seconds"] == 300


def test_metrics_bridge_main_passes_split_uncertain_thresholds(monkeypatch) -> None:
    import mail_ai_agent.metrics_bridge as metrics_bridge

    captured: dict[str, object] = {}

    def fake_build_metrics_payload(**kwargs):
        captured.update(kwargs)
        return "ok\n"

    def fake_serve_bridge(**kwargs):
        payload = kwargs["payload_builder"]()
        assert payload == "ok\n"

    monkeypatch.setattr(metrics_bridge, "build_metrics_payload", fake_build_metrics_payload)
    monkeypatch.setattr(metrics_bridge, "serve_bridge", fake_serve_bridge)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "metrics_bridge",
            "--max-uncertain",
            "3",
            "--max-uncertain-error",
            "0",
            "--max-uncertain-exploration",
            "3",
        ],
    )

    metrics_bridge.main()

    assert captured["max_uncertain"] == 3
    assert captured["max_uncertain_error"] == 0
    assert captured["max_uncertain_exploration"] == 3
