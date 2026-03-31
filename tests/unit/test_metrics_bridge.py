from __future__ import annotations

from pathlib import Path


def test_metrics_bridge_script_uses_bridge_module() -> None:
    script = Path("scripts/prod_metrics.sh").read_text(encoding="utf-8")
    assert "mail_ai_agent.metrics_bridge" in script
    assert 'ENV_FILE="${ENV_FILE:-.env.multi.prod}"' in script


def test_metrics_bridge_plist_uses_bridge_module() -> None:
    plist = Path("com.mailai.metrics.prod.plist").read_text(encoding="utf-8")
    assert "mail_ai_agent.metrics_bridge" in plist


def test_metrics_bridge_returns_500_on_payload_failure() -> None:
    import mail_ai_agent.metrics_bridge as metrics_bridge

    called: dict[str, object] = {}

    def fake_serve_metrics(*, host: str, port: int, payload_builder) -> None:
        called["host"] = host
        called["port"] = port
        called["payload_builder"] = payload_builder

    original_serve_metrics = metrics_bridge.serve_metrics
    metrics_bridge.serve_metrics = fake_serve_metrics
    try:
        metrics_bridge.serve_bridge(
            host="127.0.0.1",
            port=9177,
            payload_builder=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    finally:
        metrics_bridge.serve_metrics = original_serve_metrics

    assert called["host"] == "127.0.0.1"
    assert called["port"] == 9177
    assert callable(called["payload_builder"])
