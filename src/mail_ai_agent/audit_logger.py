from __future__ import annotations

import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    REDACTED_FIELDS = {"message_id", "sender", "subject", "draft_path"}

    def __init__(self, path: Path, *, redact_pii: bool = True) -> None:
        self.path = path
        self.redact_pii = redact_pii
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _chmod_owner_only(self.path.parent)

    def log(self, **payload: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **self._sanitize_payload(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _chmod_owner_only(self.path)

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.redact_pii:
            return payload
        sanitized = dict(payload)
        for field in self.REDACTED_FIELDS:
            value = sanitized.pop(field, None)
            if value in (None, ""):
                continue
            sanitized[f"{field}_sha256"] = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        return sanitized


def _chmod_owner_only(path: Path) -> None:
    try:
        mode = 0o700 if path.is_dir() else 0o600
        os.chmod(path, mode)
    except OSError:
        pass
