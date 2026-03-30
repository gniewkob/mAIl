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
    assert payload["message_id"] == "mid-1"


def test_audit_log_entry_readable_immediately(tmp_path):
    import json

    from mail_ai_agent.audit_logger import AuditLogger

    logger = AuditLogger(tmp_path / "audit.jsonl", redact_pii=False)
    logger.log(action="test", value="hello")
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["value"] == "hello"
