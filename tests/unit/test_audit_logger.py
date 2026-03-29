from __future__ import annotations

import json
from pathlib import Path

from mail_ai_agent.audit_logger import AuditLogger


def test_audit_logger_writes_jsonl(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")

    logger.log(level="INFO", action_taken="route_from_llm", message_id="mid-1")

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["action_taken"] == "route_from_llm"
    assert "message_id" not in payload
    assert "message_id_sha256" in payload


def test_audit_logger_can_keep_plaintext_fields_when_redaction_disabled(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl", redact_pii=False)

    logger.log(level="INFO", action_taken="route_from_llm", message_id="mid-1", sender="a@example.com")

    payload = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["message_id"] == "mid-1"
    assert payload["sender"] == "a@example.com"
