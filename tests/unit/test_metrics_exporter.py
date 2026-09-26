from __future__ import annotations

import io
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mail_ai_agent.metrics_exporter import build_metrics_payload, serve_metrics
from mail_ai_agent.state_manager import StateManager


def test_build_metrics_payload_exports_health_and_quality_metrics(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"

    manager = StateManager(state_path)
    lease = manager.acquire_lease(
        mailbox_id="kontakt",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Temat",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease.record is not None
    manager.mark_processed(
        lease.record.id,
        category="question",
        confidence=0.9,
        target_folder="INBOX.Questions",
        action_taken="move_route_from_llm",
    )
    audit_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-03-29T12:00:00+00:00",
                "mailbox_id": "kontakt",
                "category": "question",
                "action_taken": "move_route_from_llm",
                "status_after": "processed",
                "target_folder": "INBOX.Questions",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stdout_path.write_text("ok\n", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    metrics = build_metrics_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=0,
    )

    assert "mailai_health_ok 1" in metrics
    assert "mailai_state_processed 1" in metrics
    assert 'mailai_mailbox_records{mailbox_id="kontakt"} 1' in metrics
    assert 'mailai_mailbox_audit_events{mailbox_id="kontakt"} 1' in metrics
    assert 'mailai_action_records{action="move_route_from_llm"} 1' in metrics
    assert 'mailai_route_source_records{route_source="llm"} 1' in metrics
    assert 'mailai_processing_events_total{outcome="llm_routed"} 1.0' in metrics
    assert 'mailai_processing_events_total{outcome="rule_routed"} 0.0' in metrics
    assert '# HELP mailai_quality_learning_proposals Current quality-learning proposal counts by kind.' in metrics
    assert "mailai_quality_window_records_7d" in metrics
    assert "mailai_quality_window_records_14d" in metrics
    assert "mailai_quality_window_processing_failure_rate_7d" in metrics
    assert "mailai_quality_improvement_score_7d" in metrics
    assert "mailai_quality_improvement_processing_failure_delta_7d" in metrics
    assert "mailai_quality_improvement_uncertain_delta_7d" in metrics
    assert "mailai_quality_feedback_records" in metrics
    assert "mailai_quality_learning_impact_score_avg" in metrics


def test_build_metrics_payload_exports_feedback_and_impact_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    monkeypatch.setattr(
        metrics_exporter,
        "build_quality_learning_payload",
        lambda **kwargs: {
            "proposal_counts": {"prompt": 1, "parser": 1},
            "feedback_summary": {
                "records": 10,
                "wrong_total": 4,
                "wrong_rate": 0.4,
                "wrong_by_kind": {"prompt": 3, "parser": 1, "rule_engine": 0},
                "wrong_rate_by_kind": {"prompt": 0.5, "parser": 0.25, "rule_engine": 0.0},
            },
            "proposals": [
                {"kind": "prompt", "impact_score": "1.5"},
                {"kind": "parser", "impact_score": "0.5"},
            ],
        },
    )
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "quality-feedback.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "prediction_timestamp": datetime.now(timezone.utc).isoformat(),
                        "was_wrong": True,
                    }
                ),
                json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "was_wrong": False}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    (logs_dir / "exploration-tuner.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "changed": True, "applied": True}),
                json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "changed": False, "applied": False}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    metrics = build_metrics_payload(
        state_db=tmp_path / "state.sqlite",
        audit_log=tmp_path / "audit.jsonl",
        stdout_log=None,
        stderr_log=None,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=0,
    )

    assert "mailai_quality_feedback_records 10" in metrics
    assert "mailai_quality_feedback_wrong_total 4" in metrics
    assert "mailai_quality_feedback_wrong_rate 0.4" in metrics
    assert "mailai_quality_feedback_corrected_total 4" in metrics
    assert "mailai_quality_feedback_correction_share 0.4" in metrics
    assert "mailai_quality_feedback_recent_records_24h 2" in metrics
    assert "mailai_quality_feedback_recent_corrected_24h 1" in metrics
    assert "mailai_quality_feedback_time_to_correction_hours_avg" in metrics
    assert "mailai_quality_feedback_time_to_correction_hours_p50" in metrics
    assert "mailai_quality_feedback_corrections_per_1000_llm_routes_7d" in metrics
    assert "mailai_exploration_tuner_changes_14d 1.0" in metrics
    assert "mailai_exploration_tuner_runs_14d 2.0" in metrics
    assert "mailai_exploration_tuner_applied_changes_14d 1.0" in metrics
    assert 'mailai_quality_feedback_wrong_by_kind{kind="prompt"} 3' in metrics
    assert 'mailai_quality_feedback_wrong_rate_by_kind{kind="parser"} 0.25' in metrics
    assert "mailai_quality_learning_impact_score_avg 1.0" in metrics
    assert "mailai_quality_learning_impact_score_max 1.5" in metrics


def test_build_metrics_payload_exposes_latest_autotune_signals_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    autotune_dir = tmp_path / "logs" / "weekly-autotune"
    autotune_dir.mkdir(parents=True)
    (autotune_dir / "weekly-autotune-20260429T010101Z.quality.json").write_text(
        json.dumps({"signals_source_dir": "/tmp/sieve-unified-auto"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    audit_path.write_text("", encoding="utf-8")

    metrics = build_metrics_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=0,
    )

    assert 'mailai_autotune_signals_source{source="/tmp/sieve-unified-auto"} 1' in metrics


def test_build_metrics_payload_exposes_latest_deploy_verification_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    autotune_dir = tmp_path / "logs" / "weekly-autotune"
    autotune_dir.mkdir(parents=True)
    (autotune_dir / "weekly-autotune-20260430T010101Z.quality.json").write_text(
        json.dumps(
            {
                "deploy": {
                    "rollout_aborted": True,
                    "results": [
                        {"verification_mode": "explicit"},
                        {"verification_mode": "soft_pass"},
                        {"verification_mode": "soft_pass"},
                    ],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    audit_path.write_text("", encoding="utf-8")

    metrics = build_metrics_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=0,
    )

    assert 'mailai_sieve_deploy_verifications{mode="explicit"} 1' in metrics
    assert 'mailai_sieve_deploy_verifications{mode="soft_pass"} 2' in metrics
    assert "mailai_sieve_deploy_soft_pass_share 0.6666666666666666" in metrics
    assert "mailai_sieve_deploy_rollout_aborted 1" in metrics


def test_build_metrics_payload_exposes_latest_learner_mode_and_blockers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    autotune_dir = tmp_path / "logs" / "weekly-autotune"
    autotune_dir.mkdir(parents=True)
    (autotune_dir / "weekly-autotune-20260501T010101Z.quality.json").write_text(
        json.dumps(
            {
                "learner": {
                    "requested_mode": "autopromote",
                    "effective_mode": "propose",
                    "autopromote_blockers": ["state.failed > 0", "high-priority ops proposal present"],
                    "champion_challenger": {
                        "promote": False,
                        "max_impact_score": 0.41,
                        "policy_delta": 7,
                        "reasons": ["impact_score<0.5000"],
                    },
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    audit_path.write_text("", encoding="utf-8")

    metrics = build_metrics_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=0,
    )

    assert 'mailai_autotune_learner_effective_mode{mode="propose"} 1' in metrics
    assert 'mailai_autotune_learner_effective_mode{mode="autopromote"} 0' in metrics
    assert 'mailai_autotune_learner_requested_mode{mode="autopromote"} 1' in metrics
    assert 'mailai_autotune_learner_requested_mode{mode="propose"} 0' in metrics
    assert "mailai_autotune_learner_blockers_count 2" in metrics
    assert "mailai_autotune_champion_challenger_promote 0" in metrics
    assert "mailai_autotune_champion_challenger_max_impact_score 0.41" in metrics
    assert "mailai_autotune_champion_challenger_policy_delta 7" in metrics
    assert "mailai_autotune_champion_challenger_reasons_count 1" in metrics


def test_build_metrics_payload_counts_unique_mailbox_messages_from_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"

    manager = StateManager(state_path)
    lease = manager.acquire_lease(
        mailbox_id="kontakt",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Temat",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease.record is not None
    manager.mark_processed(
        lease.record.id,
        category="question",
        confidence=0.9,
        target_folder="INBOX.Questions",
        action_taken="move_route_from_llm",
    )
    audit_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-03-29T12:00:00+00:00",
                        "mailbox_id": "kontakt",
                        "category": "question",
                        "action_taken": "move_route_from_llm",
                        "status_after": "processed",
                        "target_folder": "INBOX.Questions",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-03-29T12:00:01+00:00",
                        "mailbox_id": "kontakt",
                        "action_taken": "skip_already_done",
                        "status_after": "processed",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-03-29T12:00:02+00:00",
                        "mailbox_id": "kontakt",
                        "action_taken": "skip_conflict",
                        "status_after": "processed",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stdout_path.write_text("ok\n", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    metrics = build_metrics_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=0,
    )

    assert 'mailai_mailbox_records{mailbox_id="kontakt"} 1' in metrics
    assert 'mailai_mailbox_audit_events{mailbox_id="kontakt"} 3' in metrics
    assert 'mailai_action_records{action="skip_already_done"} 1' in metrics
    assert 'mailai_action_records{action="skip_conflict"} 1' in metrics
    assert 'mailai_route_source_records{route_source="llm"} 1' in metrics
    assert 'mailai_processing_events_total{outcome="llm_routed"} 1.0' in metrics
    assert 'mailai_processing_events_total{outcome="uncertain"} 0.0' in metrics


def test_build_metrics_payload_exposes_quality_failed_for_imap_auth_failure(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    audit_path.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mailbox_id": "kontakt",
                "action_taken": "imap_auth_failed",
                "status_after": "imap_auth_failed",
                "error": "AUTHENTICATIONFAILED",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    metrics = build_metrics_payload(
        state_db=tmp_path / "state.sqlite",
        audit_log=audit_path,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=0,
    )

    assert "mailai_quality_failed 1.0" in metrics
    assert "operational_health_status 2" in metrics


def test_build_metrics_payload_exposes_watch_operational_health_for_uncertain_only(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"

    manager = StateManager(state_path)
    lease = manager.acquire_lease(
        mailbox_id="kontakt",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Temat",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease.record is not None
    manager.mark_uncertain(
        lease.record.id,
        category=None,
        confidence=None,
        error_message="manual review",
    )
    audit_path.write_text("", encoding="utf-8")
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    metrics = build_metrics_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=5,
    )

    assert "mailai_health_ok 1" in metrics
    assert "operational_health_status 1" in metrics
    assert 'mailai_mailbox_uncertain_current{mailbox_id="kontakt"} 1' in metrics
    assert "# HELP mailai_mailbox_failed_current Current failed records by mailbox from SQLite state." in metrics


def test_build_metrics_payload_emits_zero_mailbox_hotspot_series_for_manifest_mailboxes(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    env_path = tmp_path / ".env.multi.prod"
    manifest_path = tmp_path / "mailboxes.json"
    manifest_path.write_text(
        json.dumps(
            {
                "mailboxes": [
                    {"mailbox_id": "mbox_a", "imap_user": "a@example.com"},
                    {"mailbox_id": "mbox_b", "imap_user": "b@example.com"},
                ]
            }
        ),
        encoding="utf-8",
    )
    env_path.write_text(f"MAILBOXES_CONFIG_PATH={manifest_path}\n", encoding="utf-8")
    audit_path.write_text("", encoding="utf-8")

    metrics = build_metrics_payload(
        state_db=state_path,
        audit_log=audit_path,
        env_file=env_path,
        stdout_log=None,
        stderr_log=None,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=0,
    )

    assert 'mailai_mailbox_uncertain_current{mailbox_id="mbox_a"} 0' in metrics
    assert 'mailai_mailbox_uncertain_current{mailbox_id="mbox_b"} 0' in metrics
    assert 'mailai_mailbox_failed_current{mailbox_id="mbox_a"} 0' in metrics
    assert 'mailai_mailbox_failed_current{mailbox_id="mbox_b"} 0' in metrics


def test_build_metrics_payload_exposes_config_error_as_failed_health(monkeypatch, tmp_path: Path) -> None:
    from mail_ai_agent import metrics_exporter

    monkeypatch.setattr(
        metrics_exporter,
        "build_health_payload",
        lambda **kwargs: {
            "ok": False,
            "issues": ["config_error=missing keychain secret"],
            "state": {"records": 0, "processed": 0, "uncertain": 0, "failed": 0, "cleanup_pending": 0},
            "config": {"ok": False, "mailboxes_loaded": 0, "error": "missing keychain secret"},
        },
    )

    metrics = build_metrics_payload(
        state_db=tmp_path / "state.sqlite",
        audit_log=tmp_path / "audit.jsonl",
        env_file=tmp_path / ".env.multi.prod",
        stdout_log=None,
        stderr_log=None,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=0,
    )

    assert "mailai_health_ok 0" in metrics
    assert "operational_health_status 2" in metrics


def test_serve_metrics_returns_500_instead_of_eof_on_payload_failure() -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.server_address = server_address
            self.handler_cls = handler_cls
            self.closed = False
            self.status_codes: list[int] = []
            self.headers: list[tuple[str, str]] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/metrics"
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda key, value: self.headers.append((key, value))
            handler.end_headers = lambda: None
            self.handler_cls.do_GET(handler)

        def server_close(self) -> None:
            self.closed = True

    original_server_cls = metrics_exporter.ThreadingHTTPServer
    metrics_exporter.ThreadingHTTPServer = FakeServer
    try:
        serve_metrics(
            host="127.0.0.1",
            port=9177,
            payload_builder=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    finally:
        metrics_exporter.ThreadingHTTPServer = original_server_cls

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [500]
    assert ("Content-Type", "text/plain; charset=utf-8") in server.headers
    assert server.writer.buffer == b"metrics payload generation failed\n"
    assert server.closed is True


def test_serve_metrics_reuses_cached_payload_across_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            for _ in range(2):
                handler = self.handler_cls.__new__(self.handler_cls)
                handler.path = "/metrics"
                handler.headers = {}
                handler.client_address = ("127.0.0.1", 12345)
                handler.wfile = self.writer
                handler.send_response = self.status_codes.append
                handler.send_header = lambda *_args, **_kwargs: None
                handler.end_headers = lambda: None
                self.handler_cls.do_GET(handler)

        def server_close(self) -> None:
            return

    calls = {"count": 0}

    def fake_payload_builder() -> str:
        calls["count"] += 1
        return "metric_a 1\n"

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=fake_payload_builder,
        payload_refresh_seconds=0,
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [200, 200]
    assert calls["count"] == 1


def test_serve_metrics_trigger_endpoint_requires_token_and_runs_launchctl(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger"
            handler.headers = {"X-Trigger-Token": "secret"}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda *_args, **_kwargs: None
            handler.end_headers = lambda: None
            self.handler_cls.do_POST(handler)

        def server_close(self) -> None:
            return

    called: dict[str, object] = {}

    def fake_run(cmd, check, capture_output, text):
        called["cmd"] = cmd
        return None

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(metrics_exporter, "_uid", lambda: "501")
    monkeypatch.setattr(metrics_exporter.subprocess, "run", fake_run)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
        trigger_label="com.mailai.autotune.weekly.prod",
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [200]
    assert b'"ok": true' in server.writer.buffer
    assert called["cmd"] == [
        "/bin/launchctl",
        "kickstart",
        "-k",
        "gui/501/com.mailai.autotune.weekly.prod",
    ]


def test_serve_metrics_trigger_rejects_query_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger?token=secret"
            handler.headers = {}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda *_args, **_kwargs: None
            handler.end_headers = lambda: None
            self.handler_cls.do_POST(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
        trigger_label="com.mailai.autotune.weekly.prod",
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [401]
    assert b'"ok": false' in server.writer.buffer


def test_serve_metrics_trigger_rejects_non_allowlisted_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger"
            handler.headers = {"X-Trigger-Token": "secret"}
            handler.client_address = ("10.0.0.50", 12345)
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda *_args, **_kwargs: None
            handler.end_headers = lambda: None
            self.handler_cls.do_POST(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
        trigger_label="com.mailai.autotune.weekly.prod",
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [403]
    assert b'"forbidden ip"' in server.writer.buffer


def test_serve_metrics_trigger_link_allows_allowlisted_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger-link"
            handler.headers = {"Origin": "http://127.0.0.1:3000"}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda *_args, **_kwargs: None
            handler.end_headers = lambda: None
            self.handler_cls.do_POST(handler)

        def server_close(self) -> None:
            return

    called: dict[str, object] = {}

    def fake_run(cmd, check, capture_output, text):
        called["cmd"] = cmd
        return None

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(metrics_exporter, "_uid", lambda: "501")
    monkeypatch.setattr(metrics_exporter.subprocess, "run", fake_run)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
        trigger_label="com.mailai.autotune.weekly.prod",
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [200]
    assert b'"ok": true' in server.writer.buffer
    assert called["cmd"] == [
        "/bin/launchctl",
        "kickstart",
        "-k",
        "gui/501/com.mailai.autotune.weekly.prod",
    ]


def test_serve_metrics_trigger_link_rejects_non_allowlisted_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger-link"
            handler.headers = {"Origin": "http://evil.local:3000"}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda *_args, **_kwargs: None
            handler.end_headers = lambda: None
            self.handler_cls.do_POST(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
        trigger_label="com.mailai.autotune.weekly.prod",
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [403]
    assert b'"forbidden origin"' in server.writer.buffer


def test_serve_metrics_trigger_link_rejects_referer_prefix_spoof(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger-link"
            handler.headers = {"Referer": "http://127.0.0.1:3005.evil.local/dashboard"}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda *_args, **_kwargs: None
            handler.end_headers = lambda: None
            self.handler_cls.do_POST(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
        trigger_label="com.mailai.autotune.weekly.prod",
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [403]
    assert b'"forbidden origin"' in server.writer.buffer


def test_serve_metrics_trigger_link_get_returns_405(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.headers: list[tuple[str, str]] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger-link"
            handler.headers = {"Origin": "http://127.0.0.1:3005"}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda key, value: self.headers.append((key, value))
            handler.end_headers = lambda: None
            self.handler_cls.do_GET(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [405]
    assert ("Allow", "POST") in server.headers


def test_serve_metrics_trigger_get_returns_405(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.headers: list[tuple[str, str]] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger"
            handler.headers = {"X-Trigger-Token": "secret"}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda key, value: self.headers.append((key, value))
            handler.end_headers = lambda: None
            self.handler_cls.do_GET(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [405]
    assert ("Allow", "POST") in server.headers


def test_serve_metrics_trigger_ui_returns_html(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.headers: list[tuple[str, str]] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger-ui"
            handler.headers = {}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda key, value: self.headers.append((key, value))
            handler.end_headers = lambda: None
            self.handler_cls.do_GET(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [200]
    assert ("Content-Type", "text/html; charset=utf-8") in server.headers
    assert b"/autotune/trigger-link" in server.writer.buffer


def test_serve_metrics_trigger_link_returns_429_during_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            for _ in range(2):
                handler = self.handler_cls.__new__(self.handler_cls)
                handler.path = "/autotune/trigger-link"
                handler.headers = {"Origin": "http://127.0.0.1:3005"}
                handler.client_address = ("127.0.0.1", 12345)
                handler.wfile = self.writer
                handler.send_response = self.status_codes.append
                handler.send_header = lambda *_args, **_kwargs: None
                handler.end_headers = lambda: None
                self.handler_cls.do_POST(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(metrics_exporter, "_uid", lambda: "501")
    monkeypatch.setattr(metrics_exporter.subprocess, "run", lambda *args, **kwargs: None)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
        trigger_cooldown_seconds=60,
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [200, 429]


def test_serve_metrics_trigger_and_link_have_separate_cooldowns(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            token_handler = self.handler_cls.__new__(self.handler_cls)
            token_handler.path = "/autotune/trigger"
            token_handler.headers = {"X-Trigger-Token": "secret"}
            token_handler.client_address = ("127.0.0.1", 12345)
            token_handler.wfile = self.writer
            token_handler.send_response = self.status_codes.append
            token_handler.send_header = lambda *_args, **_kwargs: None
            token_handler.end_headers = lambda: None
            self.handler_cls.do_POST(token_handler)

            link_handler = self.handler_cls.__new__(self.handler_cls)
            link_handler.path = "/autotune/trigger-link"
            link_handler.headers = {"Origin": "http://127.0.0.1:3005"}
            link_handler.client_address = ("127.0.0.1", 12345)
            link_handler.wfile = self.writer
            link_handler.send_response = self.status_codes.append
            link_handler.send_header = lambda *_args, **_kwargs: None
            link_handler.end_headers = lambda: None
            self.handler_cls.do_POST(link_handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(metrics_exporter, "_uid", lambda: "501")
    monkeypatch.setattr(metrics_exporter.subprocess, "run", lambda *args, **kwargs: None)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
        trigger_cooldown_seconds=60,
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [200, 200]


def test_serve_metrics_trigger_ui_to_trigger_link_post_succeeds_for_9177_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            ui_handler = self.handler_cls.__new__(self.handler_cls)
            ui_handler.path = "/autotune/trigger-ui"
            ui_handler.headers = {}
            ui_handler.client_address = ("127.0.0.1", 12345)
            ui_handler.wfile = self.writer
            ui_handler.send_response = self.status_codes.append
            ui_handler.send_header = lambda *_args, **_kwargs: None
            ui_handler.end_headers = lambda: None
            self.handler_cls.do_GET(ui_handler)

            post_handler = self.handler_cls.__new__(self.handler_cls)
            post_handler.path = "/autotune/trigger-link"
            post_handler.headers = {"Origin": "http://127.0.0.1:9177"}
            post_handler.client_address = ("127.0.0.1", 12345)
            post_handler.wfile = self.writer
            post_handler.send_response = self.status_codes.append
            post_handler.send_header = lambda *_args, **_kwargs: None
            post_handler.end_headers = lambda: None
            self.handler_cls.do_POST(post_handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(metrics_exporter, "_uid", lambda: "501")
    monkeypatch.setattr(metrics_exporter.subprocess, "run", lambda *args, **kwargs: None)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_token="secret",
        trigger_cooldown_seconds=0,
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [200, 200]


def test_serve_metrics_trigger_link_requires_token_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger-link"
            handler.headers = {"Origin": "http://127.0.0.1:9177"}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.rfile = io.BytesIO(b"")
            handler.send_response = self.status_codes.append
            handler.send_header = lambda *_args, **_kwargs: None
            handler.end_headers = lambda: None
            self.handler_cls.do_POST(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(metrics_exporter.subprocess, "run", lambda *args, **kwargs: None)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_link_token="secret-link",
        trigger_cooldown_seconds=0,
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [401]


def test_serve_metrics_trigger_link_unauthorized_has_ip_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            for _ in range(2):
                handler = self.handler_cls.__new__(self.handler_cls)
                handler.path = "/autotune/trigger-link"
                handler.headers = {"Origin": "http://127.0.0.1:9177"}
                handler.client_address = ("127.0.0.1", 12345)
                handler.wfile = self.writer
                handler.rfile = io.BytesIO(b"")
                handler.send_response = self.status_codes.append
                handler.send_header = lambda *_args, **_kwargs: None
                handler.end_headers = lambda: None
                self.handler_cls.do_POST(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(metrics_exporter.subprocess, "run", lambda *args, **kwargs: None)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_link_token="secret-link",
        trigger_link_unauthorized_cooldown_seconds=30,
        trigger_cooldown_seconds=0,
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [401, 429]


def test_serve_metrics_trigger_link_accepts_form_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            body = b"token=secret-link"
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger-link"
            handler.headers = {"Origin": "http://127.0.0.1:9177", "Content-Length": str(len(body))}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.rfile = io.BytesIO(body)
            handler.send_response = self.status_codes.append
            handler.send_header = lambda *_args, **_kwargs: None
            handler.end_headers = lambda: None
            self.handler_cls.do_POST(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(metrics_exporter, "_uid", lambda: "501")
    monkeypatch.setattr(metrics_exporter.subprocess, "run", lambda *args, **kwargs: None)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_link_token="secret-link",
        trigger_cooldown_seconds=0,
    )

    server = FakeServer.last_instance
    assert server is not None
    assert server.status_codes == [200]


def test_serve_metrics_trigger_ui_includes_hidden_token_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            FakeServer.last_instance = self

        def serve_forever(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger-ui"
            handler.headers = {}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.send_response = self.status_codes.append
            handler.send_header = lambda *_args, **_kwargs: None
            handler.end_headers = lambda: None
            self.handler_cls.do_GET(handler)

        def server_close(self) -> None:
            return

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)
    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_link_token="secret-link",
    )
    server = FakeServer.last_instance
    assert server is not None
    assert b"name='token'" in server.writer.buffer


def test_serve_metrics_trigger_link_race_allows_only_one_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import mail_ai_agent.metrics_exporter as metrics_exporter

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""
            self.lock = threading.Lock()

        def write(self, data: bytes) -> None:
            with self.lock:
                self.buffer += data

    class FakeServer:
        last_instance = None

        def __init__(self, server_address, handler_cls) -> None:
            self.handler_cls = handler_cls
            self.status_codes: list[int] = []
            self.writer = FakeWriter()
            self.barrier = threading.Barrier(2)
            FakeServer.last_instance = self

        def _run_one(self) -> None:
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.path = "/autotune/trigger-link"
            handler.headers = {"Origin": "http://127.0.0.1:9177"}
            handler.client_address = ("127.0.0.1", 12345)
            handler.wfile = self.writer
            handler.rfile = io.BytesIO(b"")
            handler.send_response = self.status_codes.append
            handler.send_header = lambda *_args, **_kwargs: None
            handler.end_headers = lambda: None
            self.barrier.wait(timeout=1)
            self.handler_cls.do_POST(handler)

        def serve_forever(self) -> None:
            t1 = threading.Thread(target=self._run_one)
            t2 = threading.Thread(target=self._run_one)
            t1.start()
            t2.start()
            t1.join(timeout=2)
            t2.join(timeout=2)

        def server_close(self) -> None:
            return

    def fake_run(*_args, **_kwargs):
        time.sleep(0.05)
        return None

    monkeypatch.setattr(metrics_exporter, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(metrics_exporter, "_uid", lambda: "501")
    monkeypatch.setattr(metrics_exporter.subprocess, "run", fake_run)

    serve_metrics(
        host="127.0.0.1",
        port=9177,
        payload_builder=lambda: "ok\n",
        trigger_cooldown_seconds=60,
    )
    server = FakeServer.last_instance
    assert server is not None
    assert sorted(server.status_codes) == [200, 429]


def test_build_metrics_payload_emits_uncertain_split_metrics(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text("", encoding="utf-8")
    manager = StateManager(state_path)

    lease_exploration = manager.acquire_lease(
        mailbox_id="kontakt",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Temat",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease_exploration.record is not None
    manager.mark_uncertain(
        lease_exploration.record.id,
        category="other",
        confidence=0.2,
        action_taken="move_route_uncertain_exploration",
        error_message="exploration",
    )

    lease_error = manager.acquire_lease(
        mailbox_id="biuro",
        message_id="msg-2",
        fingerprint="fp-2",
        imap_uid="11",
        sender="client2@example.com",
        subject="Temat 2",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease_error.record is not None
    manager.mark_uncertain(
        lease_error.record.id,
        category="other",
        confidence=0.2,
        action_taken="move_route_uncertain_llm_failure",
        error_message="llm failed",
    )

    metrics = build_metrics_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=None,
        stderr_log=None,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=3,
        max_uncertain_error=1,
        max_uncertain_exploration=3,
    )

    assert "mailai_state_uncertain_exploration 1" in metrics
    assert "mailai_state_uncertain_error 1" in metrics
    assert 'mailai_mailbox_uncertain_exploration_current{mailbox_id="kontakt"} 1' in metrics
    assert 'mailai_mailbox_uncertain_error_current{mailbox_id="biuro"} 1' in metrics


def test_build_metrics_payload_operational_health_ignores_exploration_uncertain_only(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text("", encoding="utf-8")
    manager = StateManager(state_path)

    lease = manager.acquire_lease(
        mailbox_id="kontakt",
        message_id="msg-1",
        fingerprint="fp-1",
        imap_uid="10",
        sender="client@example.com",
        subject="Temat",
        source_folder="INBOX.AI-Review",
        internaldate=None,
        worker_id="worker-1",
        lease_seconds=60,
        max_retries=3,
    )
    assert lease.record is not None
    manager.mark_uncertain(
        lease.record.id,
        category="other",
        confidence=0.2,
        action_taken="move_route_uncertain_exploration",
        error_message="exploration",
    )

    metrics = build_metrics_payload(
        state_db=state_path,
        audit_log=audit_path,
        stdout_log=None,
        stderr_log=None,
        recent_audit_limit=10,
        recent_audit_max_age_minutes=15,
        max_uncertain=3,
        max_uncertain_error=0,
        max_uncertain_exploration=3,
    )

    assert "operational_health_status 0" in metrics
