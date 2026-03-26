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
