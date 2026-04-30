from __future__ import annotations

import json
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
