from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.config import Settings
from mail_ai_agent.weekly_autotune_cli import run_weekly_autotune
from mail_ai_agent.weekly_autotune_cli import _resolve_signals_dir


def test_resolve_signals_dir_explicit_path() -> None:
    path = _resolve_signals_dir("logs/custom-signals")
    assert path == Path("logs/custom-signals")


def test_resolve_signals_dir_auto_prefers_unified_auto(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "logs" / "sieve-unified-auto").mkdir(parents=True)
    (tmp_path / "logs" / "sieve-unified-auto" / "a.main.sieve").write_text("x", encoding="utf-8")
    (tmp_path / "logs" / "sieve-backup-2026-01-01").mkdir(parents=True)
    (tmp_path / "logs" / "sieve-backup-2026-01-01" / "b.main.sieve").write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    path = _resolve_signals_dir("auto")
    assert path == Path("logs/sieve-unified-auto")


def test_run_weekly_autotune_writes_mailbox_thresholds(tmp_path: Path) -> None:
    mailboxes = {
        "mailboxes": [
            {
                "mailbox_id": "mbox_a",
                "imap_host": "imap.example.com",
                "imap_user": "a@example.com",
                "imap_pass": "secret",
            },
            {
                "mailbox_id": "mbox_b",
                "imap_host": "imap.example.com",
                "imap_user": "b@example.com",
                "imap_pass": "secret",
            },
        ]
    }
    manifest = tmp_path / "mailboxes.json"
    manifest.write_text(json.dumps(mailboxes), encoding="utf-8")
    settings = Settings(MAILBOXES_CONFIG_PATH=str(manifest))
    audit_path = tmp_path / "audit.jsonl"
    settings.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text("", encoding="utf-8")
    settings.audit_log_path = audit_path
    settings.state_db_path = tmp_path / "state.sqlite"
    sieve_dir = tmp_path / "sieve-signals"
    sieve_dir.mkdir()
    (sieve_dir / "mbox_a.main.sieve").write_text('require ["fileinto"];\n', encoding="utf-8")

    payload = run_weekly_autotune(
        settings=settings,
        sieve_signals_dir=sieve_dir,
        auto_policy_path=tmp_path / "sieve_policy.auto.json",
        mailbox_thresholds_path=tmp_path / "mailbox_thresholds.auto.json",
        generated_sieve_dir=tmp_path / "generated",
        learning_output_dir=tmp_path / "learning",
        window_days=14,
        min_count=2,
        max_keywords_per_bucket=20,
        deploy=False,
        deploy_port=4190,
        deploy_timeout_seconds=20,
        deploy_tls_mode="auto",
        deploy_strict_verify=False,
        deploy_canary_count=1,
        deploy_canary_max_soft_pass_share=0.5,
    )
    thresholds_path = Path(payload["mailbox_thresholds_path"])
    saved = json.loads(thresholds_path.read_text(encoding="utf-8"))
    assert "mbox_a" in saved["by_mailbox"]
    assert "move_confidence_threshold" in saved["by_mailbox"]["mbox_a"]


def test_run_weekly_autotune_canary_abort_on_soft_pass(tmp_path: Path, monkeypatch) -> None:
    from mail_ai_agent import weekly_autotune_cli
    from mail_ai_agent.sieve_deploy_cli import DeployResult

    mailboxes = {
        "mailboxes": [
            {"mailbox_id": "mbox_1", "imap_host": "imap.example.com", "imap_user": "a@example.com", "imap_pass": "secret"},
            {"mailbox_id": "mbox_2", "imap_host": "imap.example.com", "imap_user": "b@example.com", "imap_pass": "secret"},
            {"mailbox_id": "mbox_3", "imap_host": "imap.example.com", "imap_user": "c@example.com", "imap_pass": "secret"},
        ]
    }
    manifest = tmp_path / "mailboxes.json"
    manifest.write_text(json.dumps(mailboxes), encoding="utf-8")
    settings = Settings(MAILBOXES_CONFIG_PATH=str(manifest))
    settings.audit_log_path = tmp_path / "audit.jsonl"
    settings.audit_log_path.write_text("", encoding="utf-8")
    settings.state_db_path = tmp_path / "state.sqlite"
    sieve_dir = tmp_path / "sieve-signals"
    sieve_dir.mkdir()
    (sieve_dir / "mbox_1.main.sieve").write_text('require ["fileinto"];\n', encoding="utf-8")

    calls: list[list[str] | None] = []

    def _fake_deploy_all(**kwargs):
        mailbox_ids = kwargs.get("mailbox_ids")
        calls.append(mailbox_ids)
        ids = mailbox_ids or ["mbox_1", "mbox_2", "mbox_3"]
        out = []
        for mailbox_id in ids:
            mode = "soft_pass" if mailbox_id == "mbox_1" else "explicit"
            out.append(
                DeployResult(
                    mailbox_id=mailbox_id,
                    host="imap.example.com",
                    user=f"{mailbox_id}@example.com",
                    script_path=str(tmp_path / f"{mailbox_id}.sieve"),
                    uploaded=True,
                    activated=True,
                    verified=True,
                    verification_mode=mode,
                )
            )
        return out

    monkeypatch.setattr(weekly_autotune_cli, "deploy_all", _fake_deploy_all)
    payload = run_weekly_autotune(
        settings=settings,
        sieve_signals_dir=sieve_dir,
        auto_policy_path=tmp_path / "sieve_policy.auto.json",
        mailbox_thresholds_path=tmp_path / "mailbox_thresholds.auto.json",
        generated_sieve_dir=tmp_path / "generated",
        learning_output_dir=tmp_path / "learning",
        window_days=14,
        min_count=2,
        max_keywords_per_bucket=20,
        deploy=True,
        deploy_port=4190,
        deploy_timeout_seconds=20,
        deploy_tls_mode="auto",
        deploy_strict_verify=False,
        deploy_canary_count=2,
        deploy_canary_max_soft_pass_share=0.4,
    )
    assert payload["deploy"]["rollout_aborted"] is True
    assert len(calls) == 1
